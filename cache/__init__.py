"""缓存与重启恢复子系统。

目录地图：
  contracts.py     CachePolicy / CacheEntry / CacheStats / CheckResult / RecoveryReport
  keys.py          key 构造与拆解、授权快照、文档指纹、策略版本、命中复核
  store.py         L1 内存 LRU + L2 PostgreSQL 双层缓存、runtime_state、TTL/LRU/容量/失效
  invalidation.py  版本感知失效（懒失效）与主动清空（演练用）
  recovery.py      启动自检与恢复报告、优雅关闭标记
  README.md        入口、key 表、状态三分类、演示与诚实边界

快速使用：
    from cache import CacheStore, build_key, run_startup_checks
    store = CacheStore()
    key, parts = build_key(question="怎么配网", authz="...", data_version="3")
    store.put(key, parts, {"hits": [...]}, now="2026-11-14T00:00:00Z")
    report = run_startup_checks(store)
    print(report.healthy, report.as_dict()["checks"])
"""
from cache.contracts import (CacheEntry, CachePolicy, CacheStats, CheckResult,
                             ORG_NAMESPACE, RecoveryReport)
from cache.invalidation import VersionController
from cache.keys import (authz_snapshot, build_key, compute_doc_fingerprint,
                        key_matches, normalize_query, policy_version, query_hash)
from cache.recovery import note_shutdown, run_startup_checks, was_graceful
from cache.store import CacheStore, utcnow

__all__ = [
    "CacheStore", "CachePolicy", "CacheEntry", "CacheStats", "CheckResult",
    "RecoveryReport", "VersionController", "ORG_NAMESPACE",
    "build_key", "key_matches", "authz_snapshot", "compute_doc_fingerprint",
    "normalize_query", "query_hash", "policy_version",
    "run_startup_checks", "note_shutdown", "was_graceful",
    "utcnow",
]
