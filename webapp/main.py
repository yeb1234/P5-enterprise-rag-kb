"""FastAPI 入口：装配路由、挂载静态前端、启动时初始化库表与演示种子。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import uuid

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
from webapp.routers_knowledge import router as knowledge_router
from webapp.routers_runtime import router as runtime_router
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
}


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

    @app.middleware("http")
    async def rate_limit_gate(request: Request, call_next):
        """三维计数（user / endpoint / model），静态资源直通。

        配额本体是「用户 / 接口 / 模型调用」三维；department 只是成本归因标签，
        不做按部门的 429 隔离断言。默认阈值宽松，不误伤演示自测；
        规则可由 admin 在评估测试页调整，命中即返回统一 429。
        """
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
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
        hit = obs.rate_check(path=path, user_id=uid, department="default", model=model)
        if hit:
            dim = hit["dimension"]
            trace_id = uuid.uuid4().hex[:16]
            # 统一错误契约：code（业务错误码）/ kind / message / detail / trace_id。
            # detail 保持字符串：前端 api() 在 app.js:56/66 直接读 j.detail 做提示。
            body = {
                "code": RATE_CODES.get(dim, "RATE_LIMIT_EXCEEDED"),
                "kind": "rate_limit_exceeded",
                "message": "请求过于频繁，请稍后重试",
                "detail": (f"请求过于频繁（{dim} 维度，{hit['limit']} 次 / {hit['window_s']}s），"
                           f"请 {hit['retry_after']}s 后重试"),
                "trace_id": trace_id,
                "policy": "rate_limit_exceeded 可退避重试；quota_exceeded 不重试",
                # 以下为向后兼容字段：评估测试子页与既有前端依赖，不删。
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
        return await call_next(request)

    app.include_router(auth_router)
    app.include_router(docs_router)
    app.include_router(chat_router)
    app.include_router(badcase_router)
    app.include_router(admin_router)
    app.include_router(ops_router)
    app.include_router(settings_router)
    app.include_router(evaltest_router)
    app.include_router(parse_router)
    app.include_router(knowledge_router)
    app.include_router(runtime_router)

    # 无构建静态前端（同源托管，免去 dev server）
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
