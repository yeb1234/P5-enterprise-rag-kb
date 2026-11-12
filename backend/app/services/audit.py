"""审计落库：登录、上传、编辑、删除、检索、导出、权限拦截全部留痕。

审计表只追加。生产建议：按月声明式分区、冷数据归档、单独账号只读授权、
对 params_hash 做完整性校验（防止事后篡改明细）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.constants import Action
from app.models.identity import AuditLog


def _params_hash(params: Any) -> str:
    if params is None:
        return ""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record(
    db: Session,
    *,
    tenant_id,
    user_id=None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    result: str = "success",
    detail: dict | None = None,
    params: Any = None,
    meta: dict | None = None,
    commit: bool = False,
) -> AuditLog:
    """默认只 flush 不 commit，跟业务操作同一个事务；独立调用时传 commit=True。"""
    meta = meta or {}
    log = AuditLog(
        tenant_id=tenant_id, user_id=user_id, action=action,
        resource_type=resource_type, resource_id=str(resource_id) if resource_id else None,
        result=result, detail=detail or {}, params_hash=_params_hash(params),
        ip=meta.get("ip"), user_agent=meta.get("user_agent"), trace_id=meta.get("trace_id"),
    )
    db.add(log)
    db.flush()
    if commit:
        db.commit()
    return log


def record_denied(db: Session, tenant_id, user_id, action: str, resource_type: str, reason: str, meta: dict) -> None:
    """权限拦截也要留痕：越权尝试是安全告警最重要的数据源。"""
    record(
        db, tenant_id=tenant_id, user_id=user_id, action=action, resource_type=resource_type,
        result="denied", detail={"reason": reason}, meta=meta,
    )
    db.commit()


SENSITIVE_ACTIONS = {
    "login", "logout", Action.UPLOAD.value, Action.DELETE.value,
    Action.EXPORT.value, Action.GRANT.value,
}
