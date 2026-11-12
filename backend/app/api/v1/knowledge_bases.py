"""知识库管理。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.core.config import settings
from app.core.constants import Action
from app.core.deps import CurrentUser, DbSession, request_meta
from app.core.errors import Forbidden, NotFound
from app.db.repository import TenantRepository
from app.models.knowledge import KnowledgeBase
from app.schemas.common import Page
from app.schemas.knowledge import KbCreate, KbOut, KbPatch
from app.services.audit import record

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


@router.get("", response_model=Page[KbOut], summary="知识库列表")
def list_kbs(db: DbSession, user: CurrentUser, page: int = 1, page_size: int = 20):
    repo = TenantRepository(db, user.tenant_id)
    items, total = repo.paginate(repo.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()), page, page_size)
    return Page(items=[KbOut.model_validate(x) for x in items], total=total, page=page, page_size=page_size)


@router.post("", response_model=KbOut, summary="创建知识库")
def create_kb(payload: KbCreate, request: Request, db: DbSession, user: CurrentUser):
    if not user.is_admin:
        raise Forbidden("仅管理员可创建知识库")
    obj = KnowledgeBase(
        tenant_id=user.tenant_id, department_id=payload.department_id or user.department_id,
        name=payload.name, description=payload.description,
        visibility=payload.visibility, default_sensitivity=payload.default_sensitivity,
        embedding_model=settings.EMBEDDING_MODEL, embedding_dim=settings.EMBEDDING_DIM,
        created_by=user.id,
    )
    db.add(obj)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.CREATE.value,
           resource_type="kb", resource_id=str(obj.id), meta=request_meta(request))
    db.commit()
    return KbOut.model_validate(obj)


@router.get("/{kb_id}", response_model=KbOut, summary="知识库详情")
def get_kb(kb_id: uuid.UUID, db: DbSession, user: CurrentUser):
    return KbOut.model_validate(TenantRepository(db, user.tenant_id).get(KnowledgeBase, kb_id))


@router.patch("/{kb_id}", response_model=KbOut, summary="修改知识库")
def patch_kb(kb_id: uuid.UUID, payload: KbPatch, request: Request, db: DbSession, user: CurrentUser):
    obj = TenantRepository(db, user.tenant_id).get(KnowledgeBase, kb_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(obj, key, value)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.UPDATE.value,
           resource_type="kb", resource_id=str(kb_id), meta=request_meta(request))
    db.commit()
    return KbOut.model_validate(obj)


@router.delete("/{kb_id}", summary="删除知识库（软删）")
def delete_kb(kb_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    obj = TenantRepository(db, user.tenant_id).get(KnowledgeBase, kb_id)
    obj.is_deleted = True
    obj.deleted_at = datetime.now(timezone.utc)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.DELETE.value,
           resource_type="kb", resource_id=str(kb_id), meta=request_meta(request))
    db.commit()
    return {"ok": True, "note": "库内文档与向量由维护任务异步清理"}
