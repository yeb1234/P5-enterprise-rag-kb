"""分布式限流：Redis 原子滑动窗口 + 内存降级实现。

问题：`observability._WINDOWS` 是**单机内存**滑动窗口，多副本部署时各副本独立计数，
实际限额被放大 N 倍，重启还清零。本模块把「计数」抽象成可替换的 `Limiter`：

- `RedisLimiter`：zset 滑动窗口，用一段 Lua 脚本一次完成
  「清理过期 → 计数 → 超限判断 → 写入 → 设过期」。**必须是单次 EVAL**，
  否则多条命令并发下会超发（读-改-写不是原子的）。
- `MemoryLimiter`：复用 `observability` 的进程内窗口，作为未配置 Redis /
  Redis 故障时的**降级实现**（与单机实现共享同一份窗口，行为一致）。

失败降级而不是把业务打挂：Redis 连不上时回退内存并记一条 WARN（每条原因只报一次）。

三态：已实现（演示级）。生产建议 Redis Cluster / 网关层令牌桶 + 全局限流。
依赖：`redis` 包**不在 requirements 里**，延迟 import；未安装时 `available()=False`。
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Protocol, runtime_checkable

# 每条降级原因只 WARN 一次，避免 Redis 抖动时刷爆日志
_WARNED: set[str] = set()


def warn_once(reason: str) -> None:
    if reason in _WARNED:
        return
    _WARNED.add(reason)
    import logging

    logging.getLogger("webapp.ratelimit").warning("rate limiter degraded: %s", reason)


@runtime_checkable
class Limiter(Protocol):
    """统一限流接口。`check` 返回 `None` 表示放行，命中返回核心计数字段。"""

    def check(self, key: str, limit: int, window_s: int, cost: int = 1) -> dict | None: ...


# ---------------------------------------------------------------------------
# Lua：原子滑动窗口
#   KEYS[1] = key
#   ARGV[1] = now(秒, float)  ARGV[2] = window_s  ARGV[3] = limit
#   ARGV[4] = cost            ARGV[5] = 唯一 member 后缀
# 返回 {allowed(1/0), current, retry_after}
# ---------------------------------------------------------------------------
_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local member = ARGV[5]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local current = redis.call('ZCARD', key)
if current + cost > limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then
    retry = math.max(1, math.ceil(window - (now - tonumber(oldest[2]))))
  end
  return {0, current, retry}
end
for i = 1, cost do
  redis.call('ZADD', key, now, member .. ':' .. i)
end
redis.call('EXPIRE', key, math.ceil(window) + 1)
return {1, current + cost, 0}
"""


class MemoryLimiter:
    """进程内滑动窗口（降级实现）。

    刻意**复用** `observability._WINDOWS` 与 `_rate_lock`：单机实现只有一份窗口，
    降级时与 `observability.rate_check` 行为一致；测试里 `obs._WINDOWS.clear()`
    也能同时清掉这份计数，不会出现"两套内存窗口互相看不见"的错觉。
    key 用 `(限流key,)` 元组，与 rate_check 的 `(dimension, value)` 不重叠。
    """

    def check(self, key: str, limit: int, window_s: int, cost: int = 1) -> dict | None:
        from webapp import observability as obs  # 延迟 import，避免模块循环

        now = time.time()
        with obs._rate_lock:
            q = obs._WINDOWS[(key,)]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) + cost > limit:
                return {
                    "limit": limit, "window_s": window_s,
                    "retry_after": max(1, int(window_s - (now - q[0]))) if q else window_s,
                    "current": len(q),
                }
            for _ in range(cost):
                q.append(now)
        return None


class RedisLimiter:
    """Redis zset 滑动窗口（单次 Lua 原子完成）。"""

    def __init__(self, url: str, redis_module: Any | None = None) -> None:
        import redis as _redis  # 延迟 import；未安装时由 _make_redis 捕获

        mod = redis_module or _redis
        self._client = mod.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=0.2, socket_timeout=0.5,
        )
        self._fallback = MemoryLimiter()

    def available(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def check(self, key: str, limit: int, window_s: int, cost: int = 1) -> dict | None:
        try:
            allowed, current, retry = self._client.eval(
                _LUA, 1, key, time.time(), window_s, limit, cost, uuid.uuid4().hex
            )
        except Exception as exc:  # 连接/超时/脚本错误：降级而不是打挂业务
            warn_once(f"redis unavailable ({type(exc).__name__})")
            return self._fallback.check(key, limit, window_s, cost)
        if int(allowed) == 1:
            return None
        return {
            "limit": limit, "window_s": window_s,
            "retry_after": max(1, int(retry)), "current": int(current),
        }


# ---------------------------------------------------------------------------
# 选择后端：WEBAPP_RATE_BACKEND = auto|memory|redis（默认 auto）
#          WEBAPP_REDIS_URL 为空 → 单机内存
# ---------------------------------------------------------------------------
_LIMITER: Limiter | None = None
_LIMITER_KEY: tuple[str, str] | None = None


def _backend() -> str:
    return (os.environ.get("WEBAPP_RATE_BACKEND") or "auto").strip().lower()


def _make_redis(url: str) -> RedisLimiter | None:
    try:
        limiter = RedisLimiter(url)
    except ImportError:
        warn_once("redis package not installed")
        return None
    except Exception as exc:
        warn_once(f"redis init failed ({type(exc).__name__})")
        return None
    if not limiter.available():
        warn_once("redis ping failed")
        return None
    return limiter


def get_limiter() -> Limiter:
    """按环境变量选择限流后端；Redis 不可用/未配置 → MemoryLimiter（并留 WARN）。"""
    global _LIMITER, _LIMITER_KEY
    url = (os.environ.get("WEBAPP_REDIS_URL") or "").strip()
    backend = _backend()
    cache_key = (backend, url)
    if _LIMITER is not None and _LIMITER_KEY == cache_key:
        return _LIMITER

    limiter: Limiter
    if backend == "memory" or not url:
        limiter = MemoryLimiter()
    elif backend in ("auto", "redis"):
        limiter = _make_redis(url) or MemoryLimiter()
    else:
        warn_once(f"unknown WEBAPP_RATE_BACKEND={backend!r}")
        limiter = MemoryLimiter()
    _LIMITER, _LIMITER_KEY = limiter, cache_key
    return limiter


# ---------------------------------------------------------------------------
# 上层编排：查规则 → 逐维计数 → 命中返回与 rate_check 一致的结构
# ---------------------------------------------------------------------------
def check_rate(*, path: str, user_id: str, model: str = "", ip: str = "",
               department: str = "default") -> dict | None:
    """分布式版 rate_check：规则/维度口径与 `observability.rate_check` 完全一致。

    `observability.rate_check` 保留不动（单机实现，既有测试仍在用）；
    本函数只把「计数」换成 `get_limiter()`，返回结构保持一致，
    因此 `main.gateway_guard` 只需换一行调用。
    """
    from webapp import observability as obs

    rules = {r["dimension"]: r for r in obs.rate_rules()}
    limiter = get_limiter()
    keys = {"user": user_id or "anonymous", "endpoint": path, "model": model or "-"}

    for dim in obs.RATE_DIMENSIONS:
        rule = rules.get(dim)
        if not rule or not rule["enabled"]:
            continue
        # 与单机实现 observability.rate_check 保持一致：model 为空表示该请求
        # 不调用模型，不能退化成 "-" 共享桶——否则全站非 chat 请求会挤进
        # 同一个 600/60s（10 QPS）桶，变成容量天花板而不是业务配额。
        if dim == "model" and not model:
            continue
        limit, win = int(rule["limit_count"]), int(rule["window_s"])
        hit = limiter.check(f"kb:rl:{dim}:{keys[dim]}", limit, win)
        if hit:
            obs._persist_hit(dim, keys[dim], path, limit, win)
            return {
                "dimension": dim, "key": keys[dim], "path": path,
                "limit": limit, "window_s": win,
                "retry_after": hit["retry_after"], "current": hit["current"],
                "department": department or "default",
            }

    if ip:
        rule = rules.get(obs.IP_DIMENSION)
        if rule and rule["enabled"]:
            limit, win = int(rule["limit_count"]), int(rule["window_s"])
            hit = limiter.check(f"kb:rl:{obs.IP_DIMENSION}:{ip}", limit, win)
            if hit:
                obs._persist_hit(obs.IP_DIMENSION, ip, path, limit, win)
                return {
                    "dimension": obs.IP_DIMENSION, "key": ip, "path": path,
                    "limit": limit, "window_s": win,
                    "retry_after": hit["retry_after"], "current": hit["current"],
                    "department": department or "default",
                }
    return None
