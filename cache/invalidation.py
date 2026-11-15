"""版本感知失效：文档 / 授权 / 策略变化时让旧 key 自然不命中。

为什么不直接 `DELETE FROM cache_entries`：
- 文档更新时批量删缓存会产生写放大，且与在途请求竞争；
- key 里已经含 `data_version` / `authz_snapshot` / `policy_version`，**递增版本后旧 key
  自然不可命中**（懒失效），TTL 到期再由 `purge_expired()` 回收空间；
- 需要立刻腾空间或做演练时，才用 `CacheStore.invalidate_all()` 主动清空。

三类触发点（与项目既有约定一致）：
1. 文档新增 / 更新 / 删除 / 重解析 → `bump_data_version()`；
2. 转岗 / 角色变更 / ACLGrant 增改 → `bump_grant_version()`；
3. 前端改新鲜度策略 / 解析策略 → `bump_policy_version()`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cache.contracts import (REASON_EXPLICIT, REASON_VERSION)


@dataclass
class InvalidationReport:
    reason: str
    old_version: str
    new_version: str
    strategy: str = "lazy"       # lazy（版本递增）| eager（主动清空）
    purged_entries: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"reason": self.reason, "old_version": self.old_version,
                "new_version": self.new_version, "strategy": self.strategy,
                "purged_entries": self.purged_entries, "notes": list(self.notes)}


DATA_VERSION_KEY = "data_version"
GRANT_VERSION_KEY = "grant_version"
POLICY_VERSION_KEY = "policy_version"
LAST_SHUTDOWN_KEY = "last_shutdown_at"
BOOT_COUNT_KEY = "boot_count"


class VersionController:
    """版本计数器，落在 `runtime_state` 表（重启不丢）。"""

    def __init__(self, store: Any):
        self.store = store

    def data_version(self) -> str:
        return self.store.state_get(DATA_VERSION_KEY, "0")

    def grant_version(self) -> str:
        return self.store.state_get(GRANT_VERSION_KEY, "0")

    def policy_version(self) -> str:
        return self.store.state_get(POLICY_VERSION_KEY, "0")

    def bump_data_version(self, *, reason: str = "document_change",
                          now: str | None = None) -> InvalidationReport:
        old = self.data_version()
        new = str(self.store.state_incr(DATA_VERSION_KEY, now=now))
        return InvalidationReport(
            reason=reason, old_version=old, new_version=new,
            notes=["旧 key 含旧 data_version，后续查询自然不命中（懒失效）",
                   "TTL 到期后由 purge_expired() 回收空间"],
        )

    def bump_grant_version(self, *, reason: str = "authz_change",
                           now: str | None = None) -> InvalidationReport:
        old = self.grant_version()
        new = str(self.store.state_incr(GRANT_VERSION_KEY, now=now))
        return InvalidationReport(
            reason=reason, old_version=old, new_version=new,
            notes=["授权快照随 grant_version 变化，旧身份的缓存不可命中"])

    def sync_policy_version(self, policy_hash: str, *, now: str | None = None) -> InvalidationReport:
        old = self.policy_version()
        if old == policy_hash:
            return InvalidationReport(reason="policy_unchanged", old_version=old,
                                      new_version=old, notes=["策略未变，无需失效"])
        self.store.state_set(POLICY_VERSION_KEY, policy_hash, now=now)
        return InvalidationReport(reason="policy_changed", old_version=old,
                                  new_version=policy_hash,
                                  notes=["策略内容哈希写入 runtime_state，重启后仍可复核"])

    def eager_purge(self, reason: str = "explicit") -> InvalidationReport:
        n = self.store.invalidate_all(reason)
        return InvalidationReport(reason=reason, old_version=self.data_version(),
                                  new_version=self.data_version(), strategy="eager",
                                  purged_entries=n,
                                  notes=["主动清空 L1+L2，用于演练或立即腾空间"])
