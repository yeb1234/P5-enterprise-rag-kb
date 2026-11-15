"""FastAPI 入口：装配路由、挂载静态前端、启动时初始化库表与演示种子。"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import db, observability as obs, security
from webapp.routers_admin import router as admin_router
from webapp.routers_auth import router as auth_router
from webapp.routers_badcase import router as badcase_router
from webapp.routers_chat import router as chat_router
from webapp.routers_docs import router as docs_router
from webapp.routers_evaltest import router as evaltest_router
from webapp.routers_evaltest import router_alias as evaltest_alias_router
from webapp.routers_knowledge import router as knowledge_router
from webapp.routers_runtime import router as runtime_router
from webapp.routers_safety import router as safety_router
from webapp.routers_ops import router as ops_router
from webapp.routers_parse import router as parse_router
from webapp.routers_settings import router as settings_router

STATIC_DIR = __import__("pathlib").Path(__file__).resolve().parent / "static"

# 统一 429 业务错误码：按命中维度区分，前端可据此给不同的重试指引。
# gateway/供应商侧的 quota_exceeded 不走这里（那类由 providers.classify_429 标记，不自动重试）。
RATE_CODES = {
    "user": "RATE_LIMIT_USER_EXCEEDED",
    "endpoint": "RATE_LIMIT_ENDPOINT_EXCEEDED",
    "model": "RATE_LIMIT_MODEL_EXCEEDED",
    # IP 是接入层防刷维度（不计入业务配额三维）；单列错误码，
    # 前端才能区分「你的账号被限流」与「这个网络出口请求过多」。
    "ip": "RATE_LIMIT_IP_EXCEEDED",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_set(name: str, default: str) -> set[str]:
    raw = os.environ.get(name, default)
    return {x.strip() for x in str(raw).split(",") if x.strip()}


# 可信代理（本机反代 / 云负载均衡回源地址）；只有直连对端命中这里才采信 XFF。
TRUSTED_PROXY_IPS: set[str] = _env_set("WEBAPP_TRUSTED_PROXY_IPS", "127.0.0.1,::1")
# 应用层全局并发闸（云 WAF 之外的自保，防突发/慢请求把 worker 打满）。
MAX_INFLIGHT = _env_int("WEBAPP_MAX_INFLIGHT", 32)
# 获取并发令牌的最长等待（秒）；测试可 monkeypatch 缩短。
INFLIGHT_ACQUIRE_TIMEOUT = 2.0
# 请求体大小上限（只查 Content-Length，不缓冲 body）。
MAX_BODY_BYTES = _env_int("WEBAPP_MAX_BODY_BYTES", 10 * 1024 * 1024)

_inflight_sem: asyncio.Semaphore | None = None
_inflight_limit: int | None = None
_inflight_loop: asyncio.AbstractEventLoop | None = None


def _inflight_semaphore() -> asyncio.Semaphore:
    """按 (事件循环, 上限) 缓存信号量。

    每次读模块级 ``MAX_INFLIGHT``，便于测试 monkeypatch；
    事件循环变了（TestClient 的 portal、asyncio.run 各建一个）就重建，
    避免把 Semaphore 绑死在已关闭的 loop 上。
    """
    global _inflight_sem, _inflight_limit, _inflight_loop
    limit = max(1, int(MAX_INFLIGHT))
    loop = asyncio.get_running_loop()
    if _inflight_sem is None or _inflight_limit != limit or _inflight_loop is not loop:
        _inflight_sem = asyncio.Semaphore(limit)
        _inflight_limit = limit
        _inflight_loop = loop
    return _inflight_sem


def _gateway_error(status: int, code: str, kind: str, message: str, detail: str,
                   headers: dict | None = None) -> JSONResponse:
    """网关层统一错误体：与 429 契约同形（code / kind / message / detail / trace_id）。"""
    trace_id = uuid.uuid4().hex[:16]
    merged = {"X-Trace-Id": trace_id}
    if headers:
        merged.update(headers)
    return JSONResponse(
        status_code=status,
        content={"code": code, "kind": kind, "message": message,
                 "detail": detail, "trace_id": trace_id},
        headers=merged,
    )


async def gateway_guard(request: Request, call_next):
    """应用层网关闸门：请求体上限 → 限流（三维 + IP 附加）→ 全局并发闸。

    只对 ``/api/`` 生效；静态资源直通、不占并发令牌。
    顺序把最便宜、最能挡量的检查放前面：超大包快速 413，被限流的请求不占并发槽。
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)

    # ① 请求体大小：只读 Content-Length，绝不整包缓冲。
    #    chunked（无 Content-Length）保持流式，由下游逐块上限兜底（见 README 已知限制）。
    declared = request.headers.get("content-length")
    if declared:
        try:
            size = int(declared)
        except ValueError:
            size = 0
        if size > MAX_BODY_BYTES:
            return _gateway_error(
                413, "PAYLOAD_TOO_LARGE", "body_size", "请求体过大",
                f"Content-Length {size} 超过上限（{MAX_BODY_BYTES} 字节）",
            )

    # ② 限流：业务配额三维（user/endpoint/model）+ IP 接入层防刷附加维度。
    uid = ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = security.verify_token(auth[7:].strip())
        if payload:
            uid = str(payload.get("sub", ""))
    model = ""
    if path.startswith("/api/settings"):
        model = "settings"
    elif path.startswith("/api/chat/"):
        model = "llm"
    peer = request.client.host if request.client else ""
    ip = security.client_ip(peer, request.headers.get("x-forwarded-for"), TRUSTED_PROXY_IPS)
    hit = obs.rate_check(path=path, user_id=uid, department="default", model=model, ip=ip)
    if hit:
        dim = hit["dimension"]
        trace_id = uuid.uuid4().hex[:16]
        # 统一错误契约：code / kind / message / detail / trace_id（既有字段一个不删）。
        body = {
            "code": RATE_CODES.get(dim, "RATE_LIMIT_EXCEEDED"),
            "kind": "rate_limit_exceeded",
            "message": "请求过于频繁，请稍后重试",
            "detail": (f"请求过于频繁（{dim} 维度，{hit['limit']} 次 / {hit['window_s']}s），"
                       f"请 {hit['retry_after']}s 后重试"),
            "trace_id": trace_id,
            "policy": "rate_limit_exceeded 可退避重试；quota_exceeded 不重试",
            # 向后兼容字段：评估测试子页与既有前端依赖，不删。
            "dimension": dim, "key": hit["key"], "path": hit["path"],
            "limit": hit["limit"], "window_s": hit["window_s"],
            "retry_after": hit["retry_after"], "current": hit["current"],
            "department": hit.get("department", "default"),
        }
        return JSONResponse(status_code=429, content=body, headers={
            "Retry-After": str(hit["retry_after"]),
            "X-RateLimit-Dimension": dim,
            "X-RateLimit-Limit": str(hit["limit"]),
            "X-RateLimit-Window": str(hit["window_s"]),
            "X-RateLimit-Code": body["code"],
            "X-Trace-Id": trace_id,
        })

    # ③ 全局并发闸：拿不到令牌就快速失败，避免雪崩。
    sem = _inflight_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=INFLIGHT_ACQUIRE_TIMEOUT)
    except asyncio.TimeoutError:
        return _gateway_error(
            503, "SERVER_BUSY", "concurrency_limit", "服务繁忙，请稍后重试",
            f"并发请求超过上限（{max(1, int(MAX_INFLIGHT))}），已快速失败",
            headers={"Retry-After": "1"},
        )

    # 释放时机：必须等响应体（含 StreamingResponse）真正发完，否则会提前放行下一个请求。
    # 用 body_iterator 包装 + try/finally，保证正常、异常、断连三条路径都恰好释放一次。
    try:
        response = await call_next(request)
    except BaseException:
        sem.release()
        raise

    inner = response.body_iterator

    async def _release_after_stream():
        try:
            async for chunk in inner:
                yield chunk
        finally:
            sem.release()

    response.body_iterator = _release_after_stream()
    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # 幂等建表 + 种入 admin 账号与评估任务
    # 启动自检：缓存表 → 恢复策略 → 孤儿任务重投 → 幽灵向量对账 → 缓存清理
    from webapp import recovery
    app.state.recovery_report = recovery.startup()
    try:
        yield
    finally:
        # 优雅关闭标记；SIGKILL 不会走到这里，因此真值层必须先落盘
        recovery.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="企业级知识库 · 可运行演示切片",
        version="0.2.0-demo",
        description="注册登录 / 文档上传管理 / 流式问答 / badcase 采集闭环 / admin 完成度，离线可跑。",
        lifespan=lifespan,
        docs_url="/api-docs",   # 与前端「文档管理」概念区分，避免 /docs 混淆
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 应用层网关闸门：请求体上限 → 限流（三维 + IP 附加）→ 全局并发闸。
    # 只对 /api/ 生效，静态资源直通；逻辑抽到模块级 gateway_guard，便于单测复用。
    app.middleware("http")(gateway_guard)

    app.include_router(auth_router)
    app.include_router(docs_router)
    app.include_router(chat_router)
    app.include_router(badcase_router)
    app.include_router(admin_router)
    app.include_router(ops_router)
    app.include_router(settings_router)
    app.include_router(evaltest_router)
    # 兼容别名 /api/eval/budget/user/{uid}（与 /api/evaltest/... 同一处理函数）
    app.include_router(evaltest_alias_router)
    app.include_router(parse_router)
    app.include_router(knowledge_router)
    app.include_router(runtime_router)
    app.include_router(safety_router)

    # 无构建静态前端（同源托管，免去 dev server）
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
