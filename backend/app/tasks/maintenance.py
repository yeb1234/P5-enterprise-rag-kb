"""定时任务：兜底扫描、日报表、跨库对账。

PG 与 Milvus 没有跨库事务，这套选型唯一的硬成本就靠这里兜：
  · 幂等键让重复写入不产生重复向量；
  · 软删 + 异步清理让删除不留下幽灵片段；
  · 每日对账比对 chunk_id 差集，补齐缺失、清理孤儿。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.integrations.embedding import get_embedding_provider
from app.integrations.milvus_client import get_vector_store
from app.integrations.object_storage import get_object_store
from app.models.identity import Tenant
from app.models.knowledge import Chunk
from app.models.pipeline import IngestJob, UploadChunk, UploadSession
from app.services.audit import record
from app.services.stats import refresh_daily_stats
from app.tasks.celery_app import celery_app

logger = get_logger("app.maintenance")


@celery_app.task(name="app.tasks.maintenance.rescue_stale_jobs")
def rescue_stale_jobs() -> dict:
    """worker 被 kill -9 时 ack 不会到、心跳停更，这类任务由 beat 捞回来重投。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    rescued = 0
    with session_scope() as db:
        stale = db.query(IngestJob).filter(
            IngestJob.state.in_(["running", "queued", "retrying"]),
            IngestJob.heartbeat_at.isnot(None),
            IngestJob.heartbeat_at < cutoff,
        ).limit(50).all()
        job_ids = []
        for job in stale:
            if job.retry_count >= job.max_retries:
                job.state = "failed"
                job.error = "心跳超时且重试次数耗尽"
            else:
                job.state = "retrying"
                job_ids.append(str(job.id))
        db.commit()
    for job_id in job_ids:
        try:
            from app.tasks.ingest import retry_failed_job

            # retry_failed_job 只接 failed，这里直接重新入队
            with session_scope() as db:
                job = db.query(IngestJob).filter(IngestJob.id == uuid.UUID(job_id)).first()
                if job:
                    job.state = "failed"
                    db.commit()
            retry_failed_job(job_id)
            rescued += 1
        except Exception as exc:
            logger.warning("rescue job=%s failed: %s", job_id, exc)
    return {"rescued": rescued}


@celery_app.task(name="app.tasks.maintenance.cleanup_expired_uploads")
def cleanup_expired_uploads() -> dict:
    now = datetime.now(timezone.utc)
    removed = 0
    with session_scope() as db:
        expired = db.query(UploadSession).filter(
            UploadSession.expires_at < now,
            UploadSession.status.in_(["initialized", "uploading", "assembling"]),
        ).limit(200).all()
        for s in expired:
            parts = db.query(UploadChunk).filter(UploadChunk.upload_id == s.id).all()
            try:
                if parts:
                    get_object_store().remove([p.object_key for p in parts])
            except Exception:
                pass
            for p in parts:
                db.delete(p)
            s.status = "expired"
            removed += 1
        db.commit()
    return {"expired_sessions": removed}


@celery_app.task(name="app.tasks.maintenance.refresh_stats")
def refresh_stats() -> dict:
    with session_scope() as db:
        tenant_ids = [r[0] for r in db.query(Tenant.id).filter(Tenant.status == "active").all()]
    for tenant_id in tenant_ids:
        with session_scope() as db:
            refresh_daily_stats(db, tenant_id)
    return {"tenants": len(tenant_ids)}


@celery_app.task(name="app.tasks.maintenance.reconcile_vectors")
def reconcile_vectors() -> dict:
    """PG ↔ Milvus 差集对账。这是「跨库双写必然不一致」的兜底证据。"""
    provider = get_embedding_provider()
    store = get_vector_store()
    summary = {"missing_fixed": 0, "orphans_removed": 0, "tenants": 0}

    with session_scope() as db:
        tenant_ids = [r[0] for r in db.query(Tenant.id).filter(Tenant.status == "active").all()]
    for tenant_id in tenant_ids:
        t = str(tenant_id)
        with session_scope() as db:
            rows = db.query(Chunk).filter(
                Chunk.tenant_id == tenant_id, Chunk.is_deleted.is_(False)
            ).yield_per(1000)
            pg_chunks = {
                str(c.id): {
                    "chunk_id": str(c.id), "tenant_id": t, "kb_id": str(c.kb_id),
                    "doc_id": str(c.document_id), "dept_id": str(c.department_id or ""),
                    "sensitivity": c.sensitivity, "version_no": c.version_no,
                    "content": c.content,
                }
                for c in rows
            }
        try:
            milvus_ids = store.list_chunk_ids(t)
        except Exception as exc:
            logger.warning("reconcile skip tenant=%s: %s", t, exc)
            continue

        missing = [pg_chunks[k] for k in set(pg_chunks) - milvus_ids if k in pg_chunks]
        for offset in range(0, len(missing), settings.EMBEDDING_BATCH_SIZE):
            batch = missing[offset:offset + settings.EMBEDDING_BATCH_SIZE]
            vectors = provider.embed([b["content"] for b in batch])
            for item, vec in zip(batch, vectors):
                item["embedding"] = vec
            try:
                store.upsert([{k: v for k, v in item.items() if k != "content"} for item in batch])
                summary["missing_fixed"] += len(batch)
            except Exception as exc:
                logger.error("reconcile upsert failed: %s", exc)

        # 孤儿向量：PG 已软删但 Milvus 还在，召回会出现幽灵片段
        orphans = milvus_ids - set(pg_chunks)
        for orphan in list(orphans)[:1000]:
            try:
                col = store._collection()
                col.delete(expr=f'chunk_id in ["{orphan}"]')
                summary["orphans_removed"] += 1
            except Exception:
                pass
        summary["tenants"] += 1
        with session_scope() as db:
            record(db, tenant_id=tenant_id, action="reconcile", resource_type="chunk",
                   result="success", detail={"missing": len(missing), "orphans": len(orphans)},
                   commit=True)
    return summary
