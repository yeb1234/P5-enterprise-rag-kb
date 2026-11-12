"""层级进度模型：batch → file → stage → unit。

三层分工（这是大文件进度方案的核心）：
  · Redis pub/sub  ：实时推送，前端 SSE 订阅，毫秒级，不落库
  · Redis snapshot ：断线重连时立刻给最近一次快照，避免进度回退/空白
  · PostgreSQL     ：真值。worker 崩溃、浏览器刷新、Redis 重启都能重建

节流：实时事件每条都推 Redis；PG 写入按 PROGRESS_THROTTLE_SECONDS 节流，
但「阶段切换 / 完成 / 失败」三类事件强制落库 —— 高频小事件不压垮业务库，
关键状态又绝不丢。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    INGEST_PHASE_WEIGHT,
    STAGE_WEIGHTS,
    UPLOAD_PHASE_WEIGHT,
    BatchStatus,
    DocStatus,
    IngestStage,
    JobState,
    UploadStatus,
)
from app.db.session import session_scope
from app.integrations.redis_client import (
    get_redis,
    progress_channel,
    progress_snapshot_key,
    publish_json,
    tenant_key,
)
from app.models.pipeline import BatchUpload, IngestJob, UploadSession
from app.schemas.upload import BatchProgress, FileProgress

TERMINAL_JOB_STATES = {JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.CANCELLED.value}


# ---------------- 纯函数：可单测，不依赖任何基础设施 ----------------

def compute_document_progress(
    stage: str,
    stage_progress: float,
    upload_progress: float = 1.0,
) -> float:
    """把「分片传输」和「后端入库」合成一个 0~100 的文档进度。

    阶段权重可解释：解析 30%、分块 10%、向量化 40%、写索引 15%、收尾 5%。
    """
    upload_progress = max(0.0, min(1.0, upload_progress))
    if stage == IngestStage.DONE.value:
        return 100.0
    done_weight = sum(
        weight for name, weight in STAGE_WEIGHTS.items()
        if name not in (IngestStage.DONE.value, IngestStage.QUEUED.value)
        and _stage_order(name) < _stage_order(stage)
    )
    current = STAGE_WEIGHTS.get(stage, 0.0) * max(0.0, min(1.0, stage_progress))
    ingest_progress = min(1.0, done_weight + current)
    return round(100 * (UPLOAD_PHASE_WEIGHT * upload_progress + INGEST_PHASE_WEIGHT * ingest_progress), 2)


_STAGE_ORDER = [
    IngestStage.QUEUED.value, IngestStage.PARSE.value, IngestStage.CHUNK.value,
    IngestStage.EMBED.value, IngestStage.INDEX.value, IngestStage.FINALIZE.value,
    IngestStage.DONE.value,
]


def _stage_order(stage: str) -> int:
    return _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else 0


def compute_batch_progress(files: list[dict]) -> float:
    """按文件体积加权：一个 500MB 的 PDF 不该和一个 2KB 的 txt 各占 50%。"""
    active = [f for f in files if f.get("status") not in ("cancelled",)]
    total_weight = sum(max(int(f.get("size_bytes", 0)), 1) for f in active)
    if not active or total_weight <= 0:
        return 0.0
    acc = sum(max(int(f.get("size_bytes", 0)), 1) * float(f.get("progress", 0)) for f in active)
    return round(acc / total_weight, 2)


# ---------------- 实时通道 ----------------

def publish_progress(tenant_id: str, batch_id: str, file_state: dict, event_type: str = "progress") -> int:
    """推一条事件到 SSE 通道，并刷新快照。返回自增 seq（用作 SSE id）。"""
    try:
        r = get_redis()
        seq = int(r.incr(tenant_key(tenant_id, "progress-seq", batch_id)))
        r.expire(tenant_key(tenant_id, "progress-seq", batch_id), 86400)
        payload = {
            "type": event_type,
            "seq": seq,
            "batch_id": batch_id,
            "file": file_state,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        r.setex(progress_snapshot_key(tenant_id, batch_id), 86400, json.dumps(payload, ensure_ascii=False, default=str))
        publish_json(progress_channel(tenant_id, batch_id), payload)
        return seq
    except Exception:
        # 推送失败不影响入库主链路，PG 快照仍可轮询
        return 0


def publish_batch_event(tenant_id: str, batch_id: str, status: str, progress: float, message: str = "") -> None:
    try:
        r = get_redis()
        seq = int(r.incr(tenant_key(tenant_id, "progress-seq", batch_id)))
        publish_json(
            progress_channel(tenant_id, batch_id),
            {"type": "batch", "seq": seq, "batch_id": batch_id, "status": status,
             "progress": progress, "message": message,
             "ts": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        pass


def publish_upload_chunk_progress(
    tenant_id: str, batch_id: str, upload_id: str, filename: str,
    received: int, total: int, size_bytes: int,
) -> None:
    fraction = received / total if total else 0.0
    publish_progress(
        tenant_id, batch_id,
        {
            "upload_id": upload_id, "filename": filename, "size_bytes": size_bytes,
            "status": UploadStatus.UPLOADING.value, "phase": "uploading",
            "stage": "upload", "progress": compute_document_progress("queued", 0, fraction),
            "stage_progress": fraction, "done_units": received, "total_units": total,
            "message": f"分片 {received}/{total}",
        },
    )


# ---------------- PG 真值 ----------------

def _should_persist(tenant_id: str, batch_id: str, document_id: str, stage: str, force: bool) -> bool:
    if force:
        return True
    try:
        r = get_redis()
        key = tenant_key(tenant_id, "progress-throttle", batch_id, document_id, stage)
        return bool(r.set(key, "1", nx=True, px=max(1, int(settings.PROGRESS_THROTTLE_SECONDS * 1000))))
    except Exception:
        return True


def report_stage(
    tenant_id: str,
    batch_id: str | None,
    document_id: str,
    stage: str,
    stage_progress: float = 0.0,
    done_units: int = 0,
    total_units: int = 0,
    message: str = "",
    state: str = JobState.RUNNING.value,
    error: str | None = None,
    celery_task_id: str | None = None,
) -> None:
    """worker 在每个阶段/每批单元完成时调用。幂等、可重放、可乱序保护。"""
    force = (
        stage in (IngestStage.DONE.value, IngestStage.FINALIZE.value)
        or state in TERMINAL_JOB_STATES
        or stage_progress >= 1.0
        or error is not None
    )
    if batch_id and not _should_persist(tenant_id, batch_id, document_id, stage, force):
        # 仍推实时事件，只是不写 PG
        publish_progress(tenant_id, batch_id, {
            "document_id": document_id, "stage": stage, "stage_progress": stage_progress,
            "progress": compute_document_progress(stage, stage_progress),
            "done_units": done_units, "total_units": total_units, "message": message,
            "status": state, "phase": "ingesting",
        })
        return

    with session_scope() as db:
        job = (
            db.query(IngestJob)
            .filter(
                IngestJob.tenant_id == uuid.UUID(tenant_id),
                IngestJob.document_id == uuid.UUID(document_id),
            )
            .order_by(IngestJob.created_at.desc())
            .first()
        )
        if job is None:
            return
        progress = compute_document_progress(stage, stage_progress)
        job.stage = stage
        job.state = state
        job.progress = progress
        job.stage_progress = stage_progress
        job.done_units = done_units
        job.total_units = total_units
        job.message = message
        job.heartbeat_at = datetime.now(timezone.utc)
        job.started_at = job.started_at or datetime.now(timezone.utc)
        if celery_task_id:
            job.celery_task_id = celery_task_id
        if error:
            job.error = error[:4000]
            job.failed_stage = stage
            job.retry_count = job.retry_count + 1
        if state in TERMINAL_JOB_STATES:
            job.finished_at = datetime.now(timezone.utc)
            if job.started_at:
                job.elapsed_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

        _sync_document_status(db, tenant_id, document_id, stage, state)
        db.flush()  # autoflush=False，必须先 flush，后面的批次聚合查询才能看到新状态
        if batch_id:
            _refresh_batch_counters(db, tenant_id, batch_id)
        db.commit()

    if batch_id:
        publish_progress(tenant_id, batch_id, {
            "document_id": document_id, "stage": stage, "stage_progress": stage_progress,
            "progress": progress, "done_units": done_units, "total_units": total_units,
            "message": message, "status": state,
            "phase": "done" if stage == IngestStage.DONE.value else "ingesting",
            "error": error,
        }, event_type="done" if stage == IngestStage.DONE.value else "progress")


def _sync_document_status(db: Session, tenant_id: str, document_id: str, stage: str, state: str) -> None:
    from app.models.knowledge import Document

    doc = db.query(Document).filter(
        Document.id == uuid.UUID(document_id), Document.tenant_id == uuid.UUID(tenant_id)
    ).first()
    if doc is None:
        return
    if state == JobState.FAILED.value:
        doc.status = DocStatus.FAILED.value
    elif stage == IngestStage.DONE.value:
        doc.status = DocStatus.INDEXED.value
        doc.indexed_at = datetime.now(timezone.utc)
    else:
        doc.status = {
            IngestStage.PARSE.value: DocStatus.PARSING.value,
            IngestStage.CHUNK.value: DocStatus.CHUNKING.value,
            IngestStage.EMBED.value: DocStatus.EMBEDDING.value,
            IngestStage.INDEX.value: DocStatus.INDEXING.value,
        }.get(stage, doc.status)


def _refresh_batch_counters(db: Session, tenant_id: str, batch_id: str) -> None:
    batch = db.query(BatchUpload).filter(
        BatchUpload.id == uuid.UUID(batch_id), BatchUpload.tenant_id == uuid.UUID(tenant_id)
    ).first()
    if batch is None:
        return
    snap = build_snapshot(db, tenant_id, batch_id, persist=False)
    batch.progress = snap.progress
    batch.succeeded = snap.succeeded
    batch.failed = snap.failed
    batch.cancelled = snap.cancelled
    batch.last_event_at = datetime.now(timezone.utc)
    if snap.succeeded + snap.failed + snap.cancelled >= snap.total_files and snap.total_files > 0:
        if snap.failed and snap.succeeded:
            batch.status = BatchStatus.PARTIAL.value
        elif snap.failed:
            batch.status = BatchStatus.FAILED.value
        else:
            batch.status = BatchStatus.COMPLETED.value
        batch.finished_at = datetime.now(timezone.utc)
        publish_batch_event(tenant_id, batch_id, batch.status, batch.progress, "批次处理完成")
    elif snap.running > 0:
        batch.status = BatchStatus.PROCESSING.value


def build_snapshot(db: Session, tenant_id: str, batch_id: str, persist: bool = True) -> BatchProgress:
    """从 PG 重建完整进度快照 —— SSE 重连、轮询降级、任务中心都用它。"""
    batch = db.query(BatchUpload).filter(
        BatchUpload.id == uuid.UUID(batch_id), BatchUpload.tenant_id == uuid.UUID(tenant_id)
    ).first()

    sessions = (
        db.query(UploadSession)
        .filter(UploadSession.tenant_id == uuid.UUID(tenant_id),
                UploadSession.batch_id == uuid.UUID(batch_id))
        .order_by(UploadSession.created_at.asc())
        .all()
    ) if batch else []

    jobs = (
        db.query(IngestJob)
        .filter(IngestJob.tenant_id == uuid.UUID(tenant_id),
                IngestJob.batch_id == uuid.UUID(batch_id))
        .order_by(IngestJob.created_at.desc())
        .all()
    ) if batch else []
    latest_job: dict[str, IngestJob] = {}
    for job in jobs:
        latest_job.setdefault(str(job.document_id), job)

    files: list[FileProgress] = []
    for s in sessions:
        job = latest_job.get(str(s.document_id)) if s.document_id else None
        if job:
            files.append(FileProgress(
                upload_id=str(s.id), document_id=str(s.document_id), filename=s.filename,
                size_bytes=s.size_bytes,
                status=job.state if job.state in TERMINAL_JOB_STATES else s.status,
                phase="done" if job.state == JobState.SUCCEEDED.value else (
                    "failed" if job.state == JobState.FAILED.value else "ingesting"),
                stage=job.stage, progress=max(job.progress, 0.0), stage_progress=job.stage_progress,
                done_units=job.done_units, total_units=job.total_units, message=job.message,
                retry_count=job.retry_count, error=job.error,
                updated_at=job.heartbeat_at.isoformat() if job.heartbeat_at else None,
            ))
        else:
            fraction = s.received_chunks / s.total_chunks if s.total_chunks else 0.0
            instant = s.status == UploadStatus.INSTANT.value
            files.append(FileProgress(
                upload_id=str(s.id), document_id=str(s.document_id), filename=s.filename,
                size_bytes=s.size_bytes,
                status=JobState.SUCCEEDED.value if instant else s.status,
                phase="done" if instant else "uploading",
                stage="done" if instant else "upload",
                progress=100.0 if instant else compute_document_progress("queued", 0, fraction),
                stage_progress=1.0 if instant else fraction,
                done_units=s.total_chunks if instant else s.received_chunks,
                total_units=s.total_chunks,
                message="秒传命中，复用已有解析结果" if instant else "等待分片上传完成",
            ))

    raw = [f.model_dump() for f in files]
    overall = compute_batch_progress(raw)
    succeeded = sum(1 for f in files if f.status == JobState.SUCCEEDED.value)
    failed = sum(1 for f in files if f.status == JobState.FAILED.value)
    cancelled = sum(1 for f in files if f.status in (JobState.CANCELLED.value, UploadStatus.CANCELLED.value))
    running = len(files) - succeeded - failed - cancelled

    if batch and persist:
        batch.progress = overall
        db.commit()

    return BatchProgress(
        batch_id=batch_id or str(uuid.uuid4()),
        status=batch.status if batch else BatchStatus.COLLECTING.value,
        progress=overall,
        total_files=batch.total_files if batch else len(files),
        succeeded=succeeded, failed=failed, cancelled=cancelled, running=max(running, 0),
        files=files,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def snapshot_from_cache(tenant_id: str, batch_id: str) -> dict | None:
    """SSE 建连瞬间先发一条缓存快照，避免用户盯着空白进度条。"""
    try:
        raw = get_redis().get(progress_snapshot_key(tenant_id, batch_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None
