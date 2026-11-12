"""知识库数据看板与统计报表。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUser, DbSession, request_meta
from app.schemas.stats import OverviewOut, ReportOut, TrendPoint
from app.services import stats as stats_service
from app.services.audit import record

router = APIRouter(prefix="/stats", tags=["看板与报表"])


@router.get("/overview", response_model=OverviewOut, summary="总览指标")
def overview(db: DbSession, user: CurrentUser):
    return stats_service.overview(db, user.tenant_id)


@router.get("/trend", response_model=list[TrendPoint], summary="趋势数据")
def trend(db: DbSession, user: CurrentUser, days: int = 14):
    return stats_service.trend(db, user.tenant_id, min(max(days, 1), 90))


@router.get("/report", response_model=ReportOut, summary="完整报表")
def report(db: DbSession, user: CurrentUser, days: int = 14):
    return stats_service.report(db, user.tenant_id, min(max(days, 1), 90))


@router.get("/export.csv", summary="导出统计报表 CSV")
def export_csv(request: Request, db: DbSession, user: CurrentUser, days: int = 14):
    """导出属于敏感操作，写审计；行数与列固定，不接受自由 SQL。"""
    rpt = stats_service.report(db, user.tenant_id, min(max(days, 1), 90))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "docs_created", "docs_indexed", "queries", "upload_bytes", "active_users"])
    for p in rpt.trend:
        writer.writerow([p.date, p.docs_created, p.docs_indexed, p.queries, p.upload_bytes, p.active_users])
    writer.writerow([])
    writer.writerow(["metric", "value"])
    for key, value in rpt.overview.model_dump().items():
        if isinstance(value, (int, float, str)):
            writer.writerow([key, value])
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="export",
           resource_type="report", result="success", detail={"days": days},
           meta=request_meta(request), commit=True)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kb-report.csv"'},
    )
