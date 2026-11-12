"""任务中心：状态查询、失败重试、取消。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.core.deps import CurrentUser, DbSession, request_meta
from app.core.errors import Conflict, NotFound
from app.db.repository import TenantRepository
from app.models.pipeline import IngestJob
from app.schemas.common import Page
from app.schemas.upload import JobOut
from app.services.audit import record
from app.services.stats import ingest_stats
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/tasks", tags=["任务中心"])


@router.get("", response_model=Page[JobOut], summary="入库任务列表")
def list_jobs(db: DbSession, user: CurrentUser, state: str | None = None,
              batch_id: uuid.UUID | None = None, page: int = 1, page_size: int = 20):
    repo = TenantRepository(db, user.tenant_id)
    q = repo.query(IngestJob)
    if state:
        q = q.filter(IngestJob.state == state)
    if batch_id:
        q = q.filter(IngestJob.batch_id == batch_id)
    items, total = repo.paginate(q.order_by(IngestJob.created_at.desc()), page, page_size)
    return Page(items=[JobOut.model_validate(x) for x in items], total=total, page=page, page_size=page_size)


@router.get("/stats", summary="任务吞吐统计")
def job_stats(db: DbSession, user: CurrentUser):
    return ingest_stats(db, user.tenant_id)


@router.post("/{job_id}/retry", summary="重试失败任务")
def retry_job(job_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    job = TenantRepository(db, user.tenant_id).get(IngestJob, job_id)
    if job.state != "failed":
        raise Conflict("仅失败任务可重试")
    from app.tasks.ingest import retry_failed_job

    task_id = retry_failed_job(str(job.id))
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="task_retry",
           resource_type="task", resource_id=str(job_id),
           detail={"celery_task_id": task_id}, meta=request_meta(request), commit=True)
    return {"ok": True, "celery_task_id": task_id}


@router.post("/{job_id}/cancel", summary="取消排队中的任务")
def cancel_job(job_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    repo = TenantRepository(db, user.tenant_id)
    job = repo.get(IngestJob, job_id)
    if job.state in ("succeeded", "failed"):
        raise Conflict("已结束的任务不能取消")
    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=False)
    job.state = "cancelled"
    job.finished_at = job.finished_at or datetime.now(timezone.utc)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="task_cancel",
           resource_type="task", resource_id=str(job_id), meta=request_meta(request))
    db.commit()
    return {"ok": True}
