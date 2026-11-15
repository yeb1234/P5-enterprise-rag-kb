"""FastAPI 入口：装配路由、挂载静态前端、启动时初始化库表与演示种子。"""
from __future__ import annotations

import asyncio
import math
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from webapp import db, observability as obs, queue as task_queue, security
from webapp.ratelimit_dist import check_rate as distributed_rate_check
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


# ---------------------------------------------------------------------------
# Prometheus 指标（手写文本，不引入 prometheus_client）
# 硬约束：绝不输出密钥 / 邮箱 / 文件名 / 用户 ID；path 动态段归一防标签爆炸。
# ---------------------------------------------------------------------------
_METRICS_LOCK = threading.Lock()
_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_METRIC_REQUESTS: dict[tuple[str, int], int] = defaultdict(int)
_METRIC_DURATION: dict[float, int] = {b: 0 for b in _DURATION_BUCKETS}
_METRIC_DURATION_SUM = 0.0
_METRIC_DURATION_COUNT = 0
_METRIC_RL_HITS: dict[str, int] = defaultdict(int)

# 纯 hex(≥6) / 纯数字 / UUID 前缀 → {id}；固定路径段（evaltest 等）不受影响
_DYNAMIC_SEG = re.compile(
    r"^(?:[0-9a-fA-F]{6,}|\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F-]+)$"
)


def normalize_path(path: str) -> str:
    """`/api/docs/abc123` → `/api/docs/{id}`，避免 Prometheus 标签爆炸。"""
    return "/".join(
        "{id}" if seg and _DYNAMIC_SEG.match(seg) else seg for seg in path.split("/")
    )


def _observe_request(path: str, status: int, elapsed: float) -> None:
    global _METRIC_DURATION_SUM, _METRIC_DURATION_COUNT
    norm = normalize_path(path)
    with _METRICS_LOCK:
        _METRIC_REQUESTS[(norm, int(status))] += 1
        _METRIC_DURATION_SUM += elapsed
        _METRIC_DURATION_COUNT += 1
        for b in _DURATION_BUCKETS:
            if elapsed <= b:
                _METRIC_DURATION[b] += 1
                break
        else:
            _METRIC_DURATION[math.inf] += 1


def _render_metrics() -> str:
    with _METRICS_LOCK:
        requests = dict(_METRIC_REQUESTS)
        durations = dict(_METRIC_DURATION)
        dur_sum, dur_count = _METRIC_DURATION_SUM, _METRIC_DURATION_COUNT
        rl_hits = dict(_METRIC_RL_HITS)

    try:
        queue_pending = int(task_queue.get_busy_state().get("queue_count", 0))
    except Exception:
        queue_pending = 0
    spend = 0.0
    doc_status: list[tuple[str, int]] = []
    try:
        conn = db.get_conn()
        try:
            spend = float(conn.execute(
                "SELECT COALESCE(SUM(cost_cny),0) AS c FROM usage_events"
            ).fetchone()["c"])
            doc_status = [(str(r["status"]), int(r["n"])) for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM documents GROUP BY status")]
        finally:
            conn.close()
    except Exception:
        pass

    lines: list[str] = []
    lines.append("# HELP kb_http_requests_total Total HTTP requests.")
    lines.append("# TYPE kb_http_requests_total counter")
    for (p, s), n in sorted(requests.items()):
        lines.append(f'kb_http_requests_total{{path="{p}",status="{s}"}} {n}')

    lines.append("# HELP kb_http_request_duration_seconds Request duration.")
    lines.append("# TYPE kb_http_request_duration_seconds histogram")
    cum = 0
    for b in _DURATION_BUCKETS:
        cum += durations.get(b, 0)
        lines.append(f'kb_http_request_duration_seconds_bucket{{le="{b}"}} {cum}')
    cum += durations.get(math.inf, 0)
    lines.append(f'kb_http_request_duration_seconds_bucket{{le="+Inf"}} {cum}')
    lines.append(f"kb_http_request_duration_seconds_sum {dur_sum:.6f}")
    lines.append(f"kb_http_request_duration_seconds_count {dur_count}")

    lines.append("# HELP kb_rate_limit_hits_total Rate-limit hits by dimension.")
    lines.append("# TYPE kb_rate_limit_hits_total counter")
    # 必须预置合法维度的零值样本：Prometheus 里「只有 HELP/TYPE、没有样本」的指标
    # 等于不存在，`rate(...)` 会一直是 no data，对应告警永不触发。
    for dim in ("user", "endpoint", "model", "ip"):
        lines.append(f'kb_rate_limit_hits_total{{dimension="{dim}"}} {rl_hits.get(dim, 0)}')
    for dim, n in sorted(rl_hits.items()):
        if dim not in ("user", "endpoint", "model", "ip"):
            lines.append(f'kb_rate_limit_hits_total{{dimension="{dim}"}} {n}')

    lines.append("# HELP kb_queue_pending Pending analysis tasks.")
    lines.append("# TYPE kb_queue_pending gauge")
    lines.append(f"kb_queue_pending {queue_pending}")

    lines.append("# HELP kb_budget_spend_cny Accumulated budget spend (CNY).")
    lines.append("# TYPE kb_budget_spend_cny gauge")
    lines.append(f"kb_budget_spend_cny {spend:.6f}")

    lines.append("# HELP kb_documents_by_status Documents grouped by status.")
    lines.append("# TYPE kb_documents_by_status gauge")
    # 同上：预置四种合法状态，空库时也有零值样本，避免指标「消失」。
    doc_map = dict(doc_status)
    for st in ("queued", "analyzing", "ready", "error"):
        lines.append(f'kb_documents_by_status{{status="{st}"}} {doc_map.get(st, 0)}')
    for status, n in sorted(doc_map.items()):
        if status not in ("queued", "analyzing", "ready", "error"):
            lines.append(f'kb_documents_by_status{{status="{status}"}} {n}')

    return "\n".join(lines) + "\n"


async def gateway_guard(request: Request, call_next):
    """网关闸门 + 请求指标。

    `/metrics` 与静态资源**直通**：不限流、不占并发令牌、也不记自身指标，
    避免 Prometheus 抓取被限流/并发闸反噬；其余 `/api/` 走
    「请求体上限 → 限流 → 并发闸」并记录指标。
    """
    path = request.url.path
    if path == "/metrics" or not path.startswith("/api/"):
        return await call_next(request)

    started = time.perf_counter()
    response = await _apply_guards(request, call_next)
    _observe_request(path, response.status_code, time.perf_counter() - started)
    return response


async def _apply_guards(request: Request, call_next):
    """实际闸门逻辑（拆出来便于 gateway_guard 统一计时 / 记指标）。"""
    path = request.url.path

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
    # 计数走 ratelimit_dist：默认内存窗口；配了 WEBAPP_REDIS_URL 则多副本共享
    # Redis zset 窗口（单次 Lua 原子），返回结构与 obs.rate_check 一致。
    hit = distributed_rate_check(path=path, user_id=uid, department="default",
                                 model=model, ip=ip)
    if hit:
        dim = hit["dimension"]
        with _METRICS_LOCK:
            _METRIC_RL_HITS[dim] += 1
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
        version="1.3.0",
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

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> PlainTextResponse:
        """Prometheus 文本指标；不登录可读，可用 WEBAPP_METRICS_ALLOW_IPS 做白名单（仅内网）。

        硬约束：不输出密钥 / 邮箱 / 文件名 / 用户 ID；path 已做动态段归一。
        生产应只暴露给 Prometheus（在网关 / 安全组层面限制来源），不要直接对公网开放。
        """
        allow = _env_set("WEBAPP_METRICS_ALLOW_IPS", "")
        if allow:
            peer = request.client.host if request.client else ""
            ip = security.client_ip(peer, request.headers.get("x-forwarded-for"),
                                    TRUSTED_PROXY_IPS)
            if ip not in allow:
                return PlainTextResponse("forbidden: metrics IP not allowed\n",
                                         status_code=403)
        return PlainTextResponse(_render_metrics(),
                                 media_type="text/plain; version=0.0.4")

    # 无构建静态前端（同源托管，免去 dev server）
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
