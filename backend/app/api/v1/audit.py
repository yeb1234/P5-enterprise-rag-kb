"""审计日志查询（只追加表，不提供修改/删除接口）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.core.errors import Forbidden
from app.db.repository import TenantRepository
from app.models.identity import AuditLog
from app.schemas.common import Page

router = APIRouter(prefix="/audit-logs", tags=["审计"])


@router.get("", response_model=Page, summary="审计日志")
def list_logs(db: DbSession, user: CurrentUser, action: str | None = None,
              resource_type: str | None = None, trace_id: str | None = None,
              page: int = 1, page_size: int = 20):
    if not user.is_admin:
        raise Forbidden("仅管理员可查看审计日志")
    repo = TenantRepository(db, user.tenant_id)
    q = repo.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if trace_id:
        q = q.filter(AuditLog.trace_id == trace_id)
    items, total = repo.paginate(q.order_by(AuditLog.created_at.desc()), page, page_size)
    return Page(items=[{
        "id": str(r.id), "user_id": str(r.user_id) if r.user_id else None,
        "action": r.action, "resource_type": r.resource_type, "resource_id": r.resource_id,
        "result": r.result, "detail": r.detail, "ip": r.ip, "trace_id": r.trace_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in items], total=total, page=page, page_size=page_size)
