"""API v1 路由汇总。限流作为路由级依赖挂一次，SSE 建连也走同一套。"""
from fastapi import APIRouter, Depends

from app.api.v1 import (
    audit,
    auth,
    categories,
    documents,
    knowledge_bases,
    search,
    stats,
    tasks,
    uploads,
    users,
    versions,
)
from app.core.config import settings
from app.middleware.rate_limit import rate_limit

api_router = APIRouter(prefix=settings.API_V1_PREFIX, dependencies=[Depends(rate_limit)])
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(categories.router)
api_router.include_router(documents.router)
api_router.include_router(versions.router)
api_router.include_router(search.router)
api_router.include_router(uploads.router)
api_router.include_router(tasks.router)
api_router.include_router(stats.router)
api_router.include_router(audit.router)
# health 路由在 main.py 挂到根路径，不走 /api/v1 前缀，供 nginx / compose 直接探测
