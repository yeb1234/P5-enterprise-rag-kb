"""按 IP 的令牌桶限流依赖。生产再加「按租户/按用户」两级，防止单租户打满 worker。"""
from __future__ import annotations

from fastapi import Request

from app.core.config import settings
from app.core.errors import AppError
from app.integrations.redis_client import tenant_key, token_bucket


def rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    key = tenant_key("global", "ratelimit", ip)
    if not token_bucket(key, settings.RATE_LIMIT_PER_MINUTE):
        raise AppError("RATE_LIMITED", "请求过于频繁，请稍后再试")
