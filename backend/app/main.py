"""FastAPI 应用入口。

分层：middleware（trace/限流/安全头）→ dependency（鉴权/租户上下文）
→ api（契约）→ service（业务）→ integration（外部系统）→ model（持久化）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.middleware.request_context import TraceIdMiddleware

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL)
    if settings.is_production and settings.JWT_SECRET_KEY.startswith("dev-only"):
        logger.error("生产环境仍在使用默认 JWT 密钥，拒绝以该配置启动")
        raise RuntimeError("JWT_SECRET_KEY 必须替换")

    # 基础设施允许「降级启动」：Milvus/MinIO 暂时没起来时 API 仍可健康检查、仍可查 PG，
    # 而不是整个容器起不来，学习期排障成本低很多。
    try:
        from app.integrations.object_storage import get_object_store

        get_object_store().ensure_bucket()
        logger.info("object storage ready")
    except Exception as exc:
        logger.warning("object storage not ready: %s", exc)
    try:
        from app.integrations.milvus_client import get_vector_store

        get_vector_store().ensure_collection()
        logger.info("milvus collection ready")
    except Exception as exc:
        logger.warning("milvus not ready, semantic search will degrade: %s", exc)
    logger.info("startup done env=%s llm=%s embedding=%s", settings.ENV,
                settings.DEEPSEEK_MODEL, settings.EMBEDDING_PROVIDER)
    yield
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="企业级知识库系统 · 开发骨架",
        version="0.1.0",
        description="React + FastAPI + PostgreSQL + Milvus + Redis/Celery + 外部 DeepSeek",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    # 纯 ASGI 中间件，不包装 SSE 响应流
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,   # 刷新令牌走 httpOnly Cookie
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-ID"],
    )
    install_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(health.router)   # /health/live、/health/ready 挂根路径

    @app.get("/", include_in_schema=False)
    def index():
        return {
            "name": settings.APP_NAME,
            "version": "0.1.0",
            "docs": "/docs",
            "bootstrap": "/api/v1/auth/bootstrap",
            "note": "开发骨架：先调用 bootstrap 初始化演示账号，再用 /api/v1/auth/login 登录",
        }

    return app


app = create_app()
