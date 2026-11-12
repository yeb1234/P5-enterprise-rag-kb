"""健康检查：liveness 与 readiness 分开。

nginx / compose 用 live 判断进程活着；ready 检查依赖，未就绪时返回 503，
让负载均衡摘掉实例但不重启它 —— 依赖抖动时反复重启只会更糟。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import SessionLocal

router = APIRouter(tags=["健康检查"])


@router.get("/health/live")
def live():
    return {"status": "alive"}


@router.get("/health/ready")
def ready():
    checks: dict[str, str] = {}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"down: {type(exc).__name__}"
    try:
        from app.integrations.redis_client import get_redis

        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"down: {type(exc).__name__}"
    try:
        from app.integrations.milvus_client import get_vector_store

        get_vector_store()._connect()
        checks["milvus"] = "ok"
    except Exception as exc:
        checks["milvus"] = f"degraded: {type(exc).__name__}"

    # Milvus 允许降级：关键词检索仍可用，不算未就绪
    healthy = checks.get("postgres") == "ok" and checks.get("redis") == "ok"
    return JSONResponse(status_code=200 if healthy else 503,
                        content={"status": "ready" if healthy else "degraded", "checks": checks})
