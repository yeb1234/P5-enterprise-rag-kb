"""Redis 统一入口：缓存、限流、进度快照、pub/sub 共用一个连接池。

key 规范：kb:{tenant_id}:{domain}:{id}
不带 tenant 前缀的 key 一律不允许写入 —— 这是缓存层串租户的唯一常见原因。
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis

from app.core.config import settings

_pool = redis.ConnectionPool.from_url(
    settings.cache_url, decode_responses=True, max_connections=64, socket_keepalive=True
)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


def tenant_key(tenant_id: str, domain: str, *parts: str) -> str:
    return f"kb:{tenant_id}:{domain}:" + ":".join(str(p) for p in parts)


def progress_channel(tenant_id: str, batch_id: str) -> str:
    return tenant_key(tenant_id, "progress-channel", batch_id)


def progress_snapshot_key(tenant_id: str, batch_id: str) -> str:
    return tenant_key(tenant_id, "progress-snapshot", batch_id)


def publish_json(channel: str, payload: dict[str, Any]) -> None:
    try:
        get_redis().publish(channel, json.dumps(payload, ensure_ascii=False, default=str))
    except redis.RedisError:
        # 进度推送是「尽力而为」：Redis 挂了不能让入库任务失败，PG 仍是真值
        pass


def set_json(key: str, value: Any, ttl: int = 3600) -> None:
    try:
        get_redis().setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
    except redis.RedisError:
        pass


def get_json(key: str) -> Any | None:
    try:
        raw = get_redis().get(key)
        return json.loads(raw) if raw else None
    except (redis.RedisError, json.JSONDecodeError):
        return None


def token_bucket(key: str, limit: int, window_seconds: int = 60) -> bool:
    """简单固定窗口计数器。生产可换 Redis Cell / 滑动窗口。"""
    try:
        r = get_redis()
        bucket = f"{key}:{int(time.time()) // window_seconds}"
        with r.pipeline() as pipe:
            pipe.incr(bucket, 1)
            pipe.expire(bucket, window_seconds + 1)
            used, _ = pipe.execute()
        return used <= limit
    except redis.RedisError:
        return True  # Redis 故障时不误伤业务，降级放行


def acquire_slot(key: str, limit: int, ttl: int = 1800) -> bool:
    """租户级并发闸：防止一个租户把 worker 队列打满。"""
    try:
        r = get_redis()
        for slot in range(limit):
            if r.set(f"{key}:{slot}", "1", nx=True, ex=ttl):
                return True
        return False
    except redis.RedisError:
        return True


def release_slot(key: str, limit: int) -> None:
    try:
        r = get_redis()
        for slot in range(limit):
            r.delete(f"{key}:{slot}")
    except redis.RedisError:
        pass
