"""FastAPI 入口：装配路由、挂载静态前端、启动时初始化库表与演示种子。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from webapp import db
from webapp.routers_admin import router as admin_router
from webapp.routers_auth import router as auth_router
from webapp.routers_badcase import router as badcase_router
from webapp.routers_chat import router as chat_router
from webapp.routers_docs import router as docs_router
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
    app.include_router(auth_router)
    app.include_router(docs_router)
    app.include_router(chat_router)
    app.include_router(badcase_router)
    app.include_router(admin_router)
    app.include_router(ops_router)
    app.include_router(settings_router)

    # 无构建静态前端（同源托管，免去 dev server）
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
