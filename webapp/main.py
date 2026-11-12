"""FastAPI 入口：装配路由、挂载静态前端、启动时初始化库表与演示种子。"""
from __future__ import annotations

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
from webapp.routers_ops import router as ops_router
from webapp.routers_settings import router as settings_router

STATIC_DIR = __import__("pathlib").Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # 幂等建表 + 种入 admin 账号与评估任务
    yield


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
        """四维限流（user / tenant / endpoint / model）。仅拦截 /api/ 写与读请求，静态资源直通。

        默认阈值宽松，不误伤演示自测；规则可由 admin 在评估测试页调整，命中即返回统一 429。
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
        hit = obs.rate_check(path=path, user_id=uid, tenant="default", model=model)
        if hit:
            body = {
                "detail": "请求过于频繁，请稍后重试",
                "dimension": hit["dimension"], "key": hit["key"], "path": hit["path"],
                "limit": hit["limit"], "window_s": hit["window_s"],
                "retry_after": hit["retry_after"], "current": hit["current"],
                "policy": "rate_limit_exceeded 可退避重试；quota_exceeded 不重试",
            }
            return JSONResponse(status_code=429, content=body, headers={
                "Retry-After": str(hit["retry_after"]),
                "X-RateLimit-Dimension": hit["dimension"],
                "X-RateLimit-Limit": str(hit["limit"]),
                "X-RateLimit-Window": str(hit["window_s"]),
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

    # 无构建静态前端（同源托管，免去 dev server）
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
