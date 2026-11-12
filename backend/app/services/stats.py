"""看板与统计报表。

口径集中在这里，避免三个页面各算一套。日报优先读 daily_stats 预聚合表，
明细表 metric_events 只用于 P95 这类需要全量排序的计算（生产应改物化视图）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.identity import AuditLog, Department
from app.models.knowledge import Chunk, Document, KnowledgeBase, Tag, Category, document_tags
from app.models.metrics import DailyStat, MetricEvent
from app.models.pipeline import IngestJob
from app.schemas.stats import (
    IngestStatsOut,
    NamedCount,
    OverviewOut,
    ReportOut,
    TrendPoint,
)


def overview(db: Session, tenant_id: uuid.UUID) -> OverviewOut:
    t = tenant_id
    today = datetime.now(timezone.utc).date()

    doc_count, indexed, failed, storage, chunk_count = db.query(
        func.count(Document.id),
        func.count(Document.id).filter(Document.status == "indexed"),
        func.count(Document.id).filter(Document.status == "failed"),
        func.coalesce(func.sum(Document.size_bytes), 0),
        func.coalesce(func.sum(Document.chunk_count), 0),
    ).filter(Document.tenant_id == t, Document.is_deleted.is_(False)).one()

    status_rows = (
        db.query(Document.status, func.count(Document.id))
        .filter(Document.tenant_id == t, Document.is_deleted.is_(False))
        .group_by(Document.status).all()
    )
    sensitivity_rows = (
        db.query(Document.sensitivity, func.count(Document.id))
        .filter(Document.tenant_id == t, Document.is_deleted.is_(False))
        .group_by(Document.sensitivity).all()
    )
    today_stat = db.query(DailyStat).filter(
        DailyStat.tenant_id == t, DailyStat.stat_date == today
    ).first()

    return OverviewOut(
        total_documents=int(doc_count or 0),
        indexed_documents=int(indexed or 0),
        failed_documents=int(failed or 0),
        total_chunks=int(chunk_count or 0),
        total_storage_bytes=int(storage or 0),
        knowledge_bases=db.query(func.count(KnowledgeBase.id)).filter(
            KnowledgeBase.tenant_id == t, KnowledgeBase.is_deleted.is_(False)).scalar() or 0,
        categories=db.query(func.count(Category.id)).filter(Category.tenant_id == t).scalar() or 0,
        queries_today=today_stat.queries if today_stat else 0,
        active_users_today=today_stat.active_users if today_stat else 0,
        ingest_success_rate=(today_stat.ingest_success_rate / 100.0) if today_stat else 0.0,
        p95_query_latency_ms=today_stat.p95_latency_ms if today_stat else 0,
        pending_jobs=db.query(func.count(IngestJob.id)).filter(
            IngestJob.tenant_id == t, IngestJob.state.in_(["queued", "running", "retrying"])
        ).scalar() or 0,
        status_distribution={str(k): int(v) for k, v in status_rows},
        sensitivity_distribution={str(k): int(v) for k, v in sensitivity_rows},
    )


def trend(db: Session, tenant_id: uuid.UUID, days: int = 14) -> list[TrendPoint]:
    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    rows = (
        db.query(DailyStat).filter(DailyStat.tenant_id == tenant_id, DailyStat.stat_date >= since)
        .order_by(DailyStat.stat_date.asc()).all()
    )
    return [
        TrendPoint(
            date=r.stat_date.isoformat(), docs_created=r.docs_created,
            docs_indexed=r.docs_indexed, queries=r.queries,
            upload_bytes=r.upload_bytes, active_users=r.active_users,
        )
        for r in rows
    ]


def by_department(db: Session, tenant_id: uuid.UUID) -> list[NamedCount]:
    rows = (
        db.query(Department.name, func.count(Document.id))
        .outerjoin(Document, Document.department_id == Department.id)
        .filter(Department.tenant_id == tenant_id)
        .group_by(Department.name).all()
    )
    return [NamedCount(name=k, value=int(v)) for k, v in rows]


def by_tag(db: Session, tenant_id: uuid.UUID) -> list[NamedCount]:
    rows = (
        db.query(Tag.name, func.count(document_tags.c.document_id))
        .join(document_tags, document_tags.c.tag_id == Tag.id)
        .filter(Tag.tenant_id == tenant_id)
        .group_by(Tag.name).order_by(func.count(document_tags.c.document_id).desc())
        .limit(20).all()
    )
    return [NamedCount(name=k, value=int(v)) for k, v in rows]


def ingest_stats(db: Session, tenant_id: uuid.UUID) -> IngestStatsOut:
    rows = db.query(IngestJob.state, func.count(IngestJob.id)).filter(
        IngestJob.tenant_id == tenant_id
    ).group_by(IngestJob.state).all()
    by_state = {str(k): int(v) for k, v in rows}
    durations = [
        int(r[0]) for r in db.query(IngestJob.elapsed_ms).filter(
            IngestJob.tenant_id == tenant_id, IngestJob.elapsed_ms.isnot(None)
        ).all()
    ]
    durations.sort()
    p95 = durations[int(len(durations) * 0.95)] if durations else 0
    stage_rows = db.query(IngestJob.stage, func.count(IngestJob.id)).filter(
        IngestJob.tenant_id == tenant_id, IngestJob.state == "running"
    ).group_by(IngestJob.stage).all()
    queue_rows = db.query(IngestJob.queue, func.count(IngestJob.id)).filter(
        IngestJob.tenant_id == tenant_id
    ).group_by(IngestJob.queue).all()
    total = sum(by_state.values())
    return IngestStatsOut(
        total_jobs=total, succeeded=by_state.get("succeeded", 0), failed=by_state.get("failed", 0),
        running=by_state.get("running", 0) + by_state.get("queued", 0),
        avg_duration_ms=int(sum(durations) / len(durations)) if durations else 0,
        p95_duration_ms=p95,
        by_stage={str(k): int(v) for k, v in stage_rows},
        by_queue={str(k): int(v) for k, v in queue_rows},
    )


def report(db: Session, tenant_id: uuid.UUID, days: int = 14) -> ReportOut:
    return ReportOut(
        overview=overview(db, tenant_id),
        trend=trend(db, tenant_id, days),
        by_department=by_department(db, tenant_id),
        by_tag=by_tag(db, tenant_id),
        ingest=ingest_stats(db, tenant_id),
    )


def refresh_daily_stats(db: Session, tenant_id: uuid.UUID, stat_date: date | None = None) -> None:
    """Celery beat 每天跑一次；也可手动触发补算历史日期。幂等（唯一键 tenant+date）。"""
    day = stat_date or datetime.now(timezone.utc).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    created = db.query(func.count(Document.id)).filter(
        Document.tenant_id == tenant_id, Document.created_at >= start, Document.created_at < end
    ).scalar() or 0
    indexed = db.query(func.count(Document.id)).filter(
        Document.tenant_id == tenant_id, Document.indexed_at >= start, Document.indexed_at < end
    ).scalar() or 0
    upload_bytes = db.query(func.coalesce(func.sum(Document.size_bytes), 0)).filter(
        Document.tenant_id == tenant_id, Document.created_at >= start, Document.created_at < end
    ).scalar() or 0
    queries, active_users = db.query(
        func.count(MetricEvent.id), func.count(func.distinct(MetricEvent.user_id))
    ).filter(MetricEvent.tenant_id == tenant_id, MetricEvent.created_at >= start,
             MetricEvent.created_at < end).one()
    jobs = db.query(
        func.count(IngestJob.id),
        func.count(IngestJob.id).filter(IngestJob.state == "succeeded"),
    ).filter(IngestJob.tenant_id == tenant_id, IngestJob.finished_at >= start,
             IngestJob.finished_at < end).one()
    success_rate = int((int(jobs[1] or 0) / max(int(jobs[0] or 0), 1)) * 10000)
    latencies = sorted(
        int(r[0]) for r in db.query(MetricEvent.latency_ms).filter(
            MetricEvent.tenant_id == tenant_id, MetricEvent.created_at >= start,
            MetricEvent.created_at < end
        ).all()
    )
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    row = db.query(DailyStat).filter(
        DailyStat.tenant_id == tenant_id, DailyStat.stat_date == day
    ).first()
    if row is None:
        row = DailyStat(tenant_id=tenant_id, stat_date=day)
        db.add(row)
    row.docs_created = int(created)
    row.docs_indexed = int(indexed)
    row.upload_bytes = int(upload_bytes)
    row.queries = int(queries or 0)
    row.active_users = int(active_users or 0)
    row.ingest_success_rate = success_rate
    row.p95_latency_ms = p95
    row.breakdown = {"by_department": [x.model_dump() for x in by_department(db, tenant_id)]}
    db.commit()
