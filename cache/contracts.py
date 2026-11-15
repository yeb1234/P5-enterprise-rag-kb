"""缓存与恢复的数据契约。

设计取舍：
- 缓存 key 的每一段都对应一类事故：缺授权快照 → 串权限，缺模型名 → 串向量，
  缺数据版本 → 旧答案，缺策略版本 → 策略改了还用旧结果。所以 key 不是「随便拼个 hash」。
- `key_parts` 与 `key` 一起返回，前端可以把 key 拆开逐段展示（面试演示的核心证据）。
- 所有判定字段可序列化、可复算；缓存条目带 `created_at` / `expires_at` / `hits`，
  便于做 TTL 淘汰、LRU 与命中率统计。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ORG_NAMESPACE = "org"          # 单组织静态命名空间，不进 DB / JWT
CACHE_SCHEMA_VERSION = "v1"    # key 结构版本，结构变了整体失效

# 失效原因（可观测要按原因分组统计）
REASON_TTL = "ttl"
REASON_VERSION = "data_version_changed"
REASON_AUTHZ = "authz_snapshot_changed"
REASON_MODEL = "model_changed"
REASON_POLICY = "policy_changed"
REASON_CAPACITY = "capacity_evict"
REASON_EXPLICIT = "explicit"
REASON_GHOST = "ghost_vector"
REASON_RESTART = "restart_rebuild"


@dataclass(frozen=True)
class CachePolicy:
    """缓存策略。阈值是演示默认经验起始值，需用真实流量校准。"""

    enabled: bool = True
    l1_capacity: int = 256            # 进程内 LRU 条目上限，重启即清空
    l2_capacity: int = 5000          # SQLite 持久化条目上限
    ttl_seconds: int = 900           # 正常结果 TTL
    negative_ttl_seconds: int = 60   # 空结果（负缓存）短 TTL，防穿透
    ttl_jitter: float = 0.10         # TTL 抖动比例，防雪崩
    org_namespace: str = ORG_NAMESPACE

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "l1_capacity": self.l1_capacity,
            "l2_capacity": self.l2_capacity, "ttl_seconds": self.ttl_seconds,
            "negative_ttl_seconds": self.negative_ttl_seconds,
            "ttl_jitter": self.ttl_jitter, "org_namespace": self.org_namespace,
            "note": "演示默认经验起始值，非压测结论，需用真实流量校准",
        }


@dataclass
class CacheEntry:
    key: str
    namespace: str
    value: Any
    data_version: str = ""
    authz_snapshot: str = ""
    policy_version: str = ""
    created_at: str = ""
    expires_at: str = ""
    last_access: str = ""
    hits: int = 0
    is_negative: bool = False
    size_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "key": self.key, "namespace": self.namespace,
            "data_version": self.data_version, "authz_snapshot": self.authz_snapshot,
            "policy_version": self.policy_version,
            "created_at": self.created_at, "expires_at": self.expires_at,
            "last_access": self.last_access, "hits": self.hits,
            "is_negative": self.is_negative, "size_bytes": self.size_bytes,
        }


@dataclass
class CacheStats:
    l1_hits: int = 0
    l2_hits: int = 0
    misses: int = 0
    writes: int = 0
    invalidations: dict[str, int] = field(default_factory=dict)
    evictions: int = 0
    ghost_purged: int = 0
    expired_purged: int = 0

    @property
    def hits(self) -> int:
        return self.l1_hits + self.l2_hits

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0

    def as_dict(self) -> dict:
        return {
            "l1_hits": self.l1_hits, "l2_hits": self.l2_hits, "misses": self.misses,
            "hits": self.hits, "hit_rate": self.hit_rate, "writes": self.writes,
            "invalidations": dict(self.invalidations), "evictions": self.evictions,
            "ghost_purged": self.ghost_purged, "expired_purged": self.expired_purged,
        }


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    count: int = 0
    category: str = ""            # discardable / rebuildable / must_persist

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "count": self.count, "category": self.category}


@dataclass
class RecoveryReport:
    started_at: str
    boot_count: int = 0
    last_shutdown_at: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    orphan_task_ids: list[str] = field(default_factory=list)
    requeued: int = 0
    ghost_vectors: int = 0
    expired_cache_purged: int = 0
    capacity_evicted: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(c.passed for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at, "boot_count": self.boot_count,
            "last_shutdown_at": self.last_shutdown_at,
            "healthy": self.healthy,
            "checks": [c.as_dict() for c in self.checks],
            "orphan_task_ids": list(self.orphan_task_ids),
            "requeued": self.requeued, "ghost_vectors": self.ghost_vectors,
            "expired_cache_purged": self.expired_cache_purged,
            "capacity_evicted": self.capacity_evicted,
            "notes": list(self.notes),
        }
