"""进程内“繁忙/排队”调度：Celery + Redis/RabbitMQ 的演示级替身。

职责边界（与 next-version 方案一致）：
- 这里只负责“分析任务能不能立刻跑 / 排队到几号”，不负责结果真值（真值在 SQLite）。
- MAX_CONCURRENT_INGEST 故意设小，方便演示“上传分析繁忙，请排队”的真实体感。
- 生产里把本模块替换为 Celery worker + broker；前端看到的 /api/system/busy 契约保持不变。
"""
from __future__ import annotations

import threading

from webapp import rag

MAX_CONCURRENT_INGEST = 2  # 小阈值，便于演示排队

_sem = threading.Semaphore(MAX_CONCURRENT_INGEST)
_lock = threading.Lock()
_active: dict[str, str] = {}   # doc_id -> filename（正在分析）
_queued: list[dict] = []        # [{id, filename}]（排队中）


def enqueue(doc_id: str, filename: str) -> None:
    with _lock:
        _queued.append({"id": doc_id, "filename": filename})
    t = threading.Thread(target=_run, args=(doc_id, filename), daemon=True)
    t.start()


def _run(doc_id: str, filename: str) -> None:
    _sem.acquire()
    try:
        with _lock:
            _queued[:] = [q for q in _queued if q["id"] != doc_id]
            _active[doc_id] = filename
        rag.analyze_document(doc_id)
    finally:
        with _lock:
            _active.pop(doc_id, None)
        _sem.release()


def get_busy_state() -> dict:
    with _lock:
        active = [{"id": k, "filename": v} for k, v in _active.items()]
        queued = list(_queued)
    busy = bool(active) or bool(queued)
    if busy:
        msg = (
            f"上传分析繁忙：{len(active)} 个任务处理中，{len(queued)} 个排队中，"
            "新上传将依次等待。"
        )
    else:
        msg = "系统空闲，可即时分析。"
    return {
        "busy": busy,
        "active_count": len(active),
        "queue_count": len(queued),
        "active": active,
        "queued": queued,
        "message": msg,
    }
