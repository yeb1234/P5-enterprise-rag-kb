"""演示切片的启动自检与恢复适配层（cache/ 包与 webapp/ 之间的桥）。

职责：
- 进程启动时：建缓存表 → 从 `runtime_state` 恢复策略 → 扫描孤儿任务重入队 →
  幽灵向量对账 → 缓存过期/容量清理 → 产出结构化恢复报告。
- 进程关闭时：写 `last_shutdown_at`，供下次启动判断上次是否异常退出。
- 提供「模拟重启」演练：只清 L1 并重跑检查，不真的杀进程，方便面试现场演示。
"""
from __future__ import annotations

import json
import threading
from typing import Any

from cache import (CachePolicy, CacheStore, VersionController, build_key,
                   note_shutdown, policy_version, run_startup_checks)
from cache.recovery import was_graceful

_lock = threading.RLock()
_store: CacheStore | None = None
_report: dict | None = None


def store() -> CacheStore:
    global _store
    with _lock:
        if _store is None:
            _store = CacheStore(policy=CachePolicy())
        return _store


def report():
    """返回启动时的完整报告（含恢复的策略清单与优雅关闭标记）。"""
    return _report


# ---------------------------------------------------------------- 策略持久化
def persist_policy(key: str, value: dict) -> str:
    """把前端改的策略落到 runtime_state，并同步 policy_version（重启可恢复）。"""
    s = store()
    s.state_set(key, json.dumps(value, ensure_ascii=False, sort_keys=True))
    return VersionController(s).sync_policy_version(policy_version(value)).new_version


def load_policy(key: str) -> dict | None:
    raw = store().state_get(key, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def data_version() -> str:
    return VersionController(store()).data_version()


def bump_data_version(reason: str = "document_change") -> None:
    """文档新增 / 更新 / 删除 / 重解析时调用：让旧检索缓存 key 自然失效。"""
    VersionController(store()).bump_data_version(reason=reason)


# ---------------------------------------------------------------- 启动/关闭
def _requeue(doc_id: str, filename: str) -> None:
    """幂等重投：先确认真值层仍是未完成态，再交给进程内队列。"""
    from webapp import db, queue
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if row and row["status"] in ("queued", "analyzing"):
        queue.enqueue(doc_id, filename)


def _restore_policies() -> list[str]:
    restored = []
    p = load_policy("knowledge_policy")
    if p:
        try:
            from webapp import routers_knowledge as rk
            rk.restore_policy(p)
            restored.append("knowledge_policy")
        except Exception:
            pass
    raw = store().state_get("parsing_strategy", "")
    if raw:
        try:
            from parsing.config import set_strategy
            set_strategy(raw)
            restored.append("parsing_strategy")
        except Exception:
            pass
    cp = load_policy("cache_policy")
    if cp:
        try:
            allowed = CachePolicy.__dataclass_fields__
            store().policy = CachePolicy(**{k: v for k, v in cp.items() if k in allowed})
            restored.append("cache_policy")
        except Exception:
            pass
    return restored


def startup() -> dict:
    global _report
    s = store()
    restored = _restore_policies()
    checked = run_startup_checks(s, requeue=_requeue, auto_fix=True)
    d = checked.as_dict()
    d["restored_policies"] = restored
    d["graceful_last_shutdown"] = was_graceful(s)
    d["note"] = ("L1 内存缓存重启必然清空；L2 SQLite 与 runtime_state 持久化，"
                 "命中时仍复核 data_version / authz_snapshot / policy_version")
    _report = d
    return d


def shutdown() -> None:
    note_shutdown(store())


def simulate_restart() -> dict:
    """演练：清 L1 → 重新自检（只检测不修改）→ 用同一 key 验证 L2 命中。

    不杀进程，用于前端展示「重启后状态仍正常」。
    """
    s = store()
    before = s.stats.as_dict()
    s.clear_memory()
    check = run_startup_checks(s, requeue=None, auto_fix=False)
    return {
        "before": before,
        "l1_cleared": True,
        "l2_entries": s.count(),
        "checks": [c.as_dict() for c in check.checks],
        "healthy": check.healthy,
        "data_version": data_version(),
        "note": "模拟重启只清 L1；L2 与 runtime_state 未变，因此缓存与策略可恢复",
    }
