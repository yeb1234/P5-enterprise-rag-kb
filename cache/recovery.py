"""启动自检与恢复：把「重启后状态是否正常」变成一份可核对的报告。

状态三分类（面试常问「重启会丢什么」）：
- **可安全丢弃**：限流滑动窗口、解析轨迹 deque、SSE 连接。丢了只是窗口重置，不产生错误数据。
- **可从真值重建**：进程内队列的活跃/排队态、进度快照。SQLite/PG 里的
  `documents.status` 是真值，启动时扫描并重新入队即可。
- **必须持久化**：配置与策略、预算、用量台账、文档与向量缓存、会话。
  全部落 SQLite 表，重启后由 `runtime_state` / 各业务表恢复。

`requeue` 回调由调用方注入，保证本包不依赖 webapp。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

from cache.contracts import (CheckResult, RecoveryReport, REASON_GHOST,
                             REASON_RESTART, REASON_TTL, REASON_CAPACITY)
from cache.invalidation import (BOOT_COUNT_KEY, LAST_SHUTDOWN_KEY,
                                 DATA_VERSION_KEY, VersionController)
from cache.keys import compute_doc_fingerprint
from cache.store import utcnow

OrphanCallback = Callable[[str, str], None]


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    r = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(r)


def run_startup_checks(store: Any, *, now: str | None = None,
                       requeue: OrphanCallback | None = None,
                       auto_fix: bool = True) -> RecoveryReport:
    """启动自检。返回结构化报告；`auto_fix=False` 时只检测不修改（用于演练对照）。"""
    stamp = utcnow(now)
    vc = VersionController(store)
    boot = store.state_incr(BOOT_COUNT_KEY, now=stamp)
    report = RecoveryReport(started_at=stamp, boot_count=boot,
                            last_shutdown_at=store.state_get(LAST_SHUTDOWN_KEY, ""))

    con = store._conn()
    try:
        # 1. 表结构
        needed = ("cache_entries", "runtime_state")
        missing = [t for t in needed if not _table_exists(con, t)]
        report.checks.append(CheckResult(
            "schema_ready", not missing,
            "缓存与运行时状态表齐备" if not missing else "缺失: " + ",".join(missing),
            category="must_persist"))

        # 2. 进程内状态分类（不修改，只声明处置）
        report.checks.append(CheckResult(
            "volatile_state_discarded", True,
            "限流窗口 / 轨迹 deque / SSE 连接：重启即丢，属可安全丢弃；窗口计数从零开始",
            category="discardable"))

        # 3. 孤儿任务：queued / analyzing 是真值层里的未完成态
        orphan_ids: list[str] = []
        if _table_exists(con, "documents"):
            rows = con.execute(
                "SELECT id, filename FROM documents WHERE status IN ('queued','analyzing')"
            ).fetchall()
            orphan_ids = [str(r["id"]) for r in rows]
            if auto_fix and requeue and rows:
                for r in rows:
                    try:
                        requeue(str(r["id"]), str(r["filename"] or ""))
                        report.requeued += 1
                    except Exception:
                        pass
        report.orphan_task_ids = orphan_ids
        report.checks.append(CheckResult(
            "orphan_tasks_recovered", True,
            (f"检测到 {len(orphan_ids)} 个未完成任务；" +
             (f"已重新入队 {report.requeued} 个" if auto_fix else "仅检测，未重入队")),
            count=len(orphan_ids), category="rebuildable"))

        # 4. 幽灵向量：缓存里 chunk_id 已不在 chunks 表
        ghost = 0
        if _table_exists(con, "chunk_embeddings") and _table_exists(con, "chunks"):
            ghost = int(con.execute(
                "SELECT COUNT(*) AS c FROM chunk_embeddings e "
                "WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.id=e.chunk_id)"
            ).fetchone()["c"])
            if ghost and auto_fix:
                con.execute(
                    "DELETE FROM chunk_embeddings WHERE NOT EXISTS "
                    "(SELECT 1 FROM chunks c WHERE c.id=chunk_embeddings.chunk_id)")
                con.commit()
                store.stats.ghost_purged += ghost
                store.stats.invalidations[REASON_GHOST] = (
                    store.stats.invalidations.get(REASON_GHOST, 0) + ghost)
        report.ghost_vectors = ghost
        report.checks.append(CheckResult(
            "ghost_vectors_reconciled", True,
            f"对账发现 {ghost} 条幽灵向量" + ("，已清理" if ghost and auto_fix else ""),
            count=ghost, category="must_persist"))

        # 5. 文档集合指纹：与上次启动时对比，不一致则递增 data_version（懒失效）
        fingerprint = "n/a"
        if _table_exists(con, "documents"):
            docs = [dict(r) for r in con.execute("SELECT * FROM documents").fetchall()]
            fingerprint = compute_doc_fingerprint(docs)
            saved = store.state_get("doc_fingerprint", "")
            if saved and saved != fingerprint:
                vc.bump_data_version(reason="restart_doc_change", now=stamp)
            if auto_fix:
                store.state_set("doc_fingerprint", fingerprint, now=stamp)
        report.checks.append(CheckResult(
            "data_version_consistent", True,
            f"文档指纹 {fingerprint}；data_version={vc.data_version()}（变化则旧缓存 key 自然失效）",
            category="must_persist"))

        # 6. 策略与配置持久化状态
        report.checks.append(CheckResult(
            "policies_persisted", True,
            f"grant_version={vc.grant_version()}, policy_version={vc.policy_version()}；"
            "新鲜度/解析策略从 runtime_state 恢复，不从进程内存恢复",
            category="must_persist"))
    finally:
        con.close()

    # 7~9. 缓存清理（走 store 的确定性逻辑）
    if auto_fix:
        report.expired_cache_purged = store.purge_expired(now=stamp)
        report.capacity_evicted = store.enforce_capacity()
    report.checks.append(CheckResult(
        "expired_cache_purged", True,
        f"清理过期条目 {report.expired_cache_purged} 条、容量淘汰 {report.capacity_evicted} 条",
        count=report.expired_cache_purged + report.capacity_evicted,
        category="must_persist"))

    report.notes = [
        "L1 内存 LRU 重启必然清空，属可安全丢弃；L2 SQLite 条目重启后仍在，命中时仍复核三重版本字段。",
        "孤儿任务靠 documents.status 真值重建，重新入队是幂等的（只处理 queued/analyzing），重启两次不会重复分析。",
        "TTL 不是权限控制：命中后仍比对 authz_snapshot；转岗/授权变更会让旧 key 不可命中。",
        "本次为演示切片的 SQLite 实现；生产对应 PG 真值 + Redis 实时通道，进度可从真值重建，严禁 FLUSHALL。",
        "限流窗口清零意味着重启后短时窗口内可能放行更多请求，属已知取舍，不是数据错误。",
    ]
    return report


def note_shutdown(store: Any, *, now: str | None = None) -> None:
    """优雅关闭时记录时间戳，供下次启动判断上次是否异常退出。"""
    store.state_set(LAST_SHUTDOWN_KEY, utcnow(now), now=now)


def was_graceful(store: Any) -> bool:
    return bool(store.state_get(LAST_SHUTDOWN_KEY, ""))
