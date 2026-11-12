"""版本历史与对比。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.core.deps import CurrentUser, DbSession, request_meta
from app.db.repository import TenantRepository
from app.models.knowledge import Document
from app.schemas.common import DiffResult
from app.schemas.knowledge import CompareRequest, VersionDetail, VersionOut
from app.services import version as version_service
from app.services.audit import record

router = APIRouter(prefix="/documents/{doc_id}/versions", tags=["版本管理"])


@router.get("", response_model=list[VersionOut], summary="版本历史")
def versions(doc_id: uuid.UUID, db: DbSession, user: CurrentUser):
    TenantRepository(db, user.tenant_id).get(Document, doc_id)
    return version_service.list_versions(db, user.tenant_id, doc_id)


@router.get("/{version_no}", response_model=VersionDetail, summary="版本详情（含正文）")
def version_detail(doc_id: uuid.UUID, version_no: int, db: DbSession, user: CurrentUser):
    return version_service.get_version(db, user.tenant_id, doc_id, version_no)


@router.post("/compare", response_model=DiffResult, summary="两个版本的行级 diff")
def compare(doc_id: uuid.UUID, payload: CompareRequest, db: DbSession, user: CurrentUser):
    TenantRepository(db, user.tenant_id).get(Document, doc_id)
    return version_service.compare(db, user.tenant_id, doc_id, payload.from_version, payload.to_version)


@router.post("/{version_no}/restore", response_model=VersionOut, summary="回滚到指定版本（追加新版本）")
def restore(doc_id: uuid.UUID, version_no: int, request: Request, db: DbSession, user: CurrentUser):
    doc = TenantRepository(db, user.tenant_id).get(Document, doc_id)
    version = version_service.restore(db, user, doc, version_no)
    from app.tasks.ingest import enqueue_richtext_ingest

    enqueue_richtext_ingest(
        tenant_id=str(user.tenant_id), document_id=str(doc.id),
        kb_id=str(doc.kb_id), version_no=doc.current_version,
    )
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="restore_version",
           resource_type="document", resource_id=str(doc_id),
           detail={"from": version_no, "to": doc.current_version},
           meta=request_meta(request), commit=True)
    return VersionOut.model_validate(version)
