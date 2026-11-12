"""文档资源接口：列表/创建/编辑/删除/恢复/下载/重新索引。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.constants import Action
from app.core.deps import CurrentUser, DbSession, request_meta
from app.core.errors import Forbidden
from app.db.repository import TenantRepository
from app.integrations.object_storage import get_object_store
from app.models.knowledge import Document
from app.models.pipeline import IngestJob
from app.schemas.common import OkResponse, Page
from app.schemas.knowledge import DocumentCreate, DocumentListFilter, DocumentOut, DocumentPatch
from app.services import document as doc_service
from app.services.audit import record
from app.services.permission import PermissionService, Resource

router = APIRouter(prefix="/documents", tags=["文档"])


@router.get("", response_model=Page[DocumentOut], summary="文档列表（全文 + 标签 + 属性 + 分类筛选）")
def list_documents(
    db: DbSession, user: CurrentUser,
    q: str | None = None, kb_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None, tag_id: uuid.UUID | None = None,
    status: str | None = None, sensitivity_max: str | None = None,
    attribute_key: str | None = None, attribute_value: str | None = None,
    include_descendants: bool = True,
    page: int = 1, page_size: int = 20,
):
    filters = DocumentListFilter(
        query=q, kb_ids=[kb_id] if kb_id else [],
        category_ids=[category_id] if category_id else [],
        tag_ids=[tag_id] if tag_id else [],
        attributes={attribute_key: attribute_value} if attribute_key and attribute_value else {},
        status=status, sensitivity_max=sensitivity_max,
        include_descendants=include_descendants, page=page, page_size=page_size,
    )
    items, total = doc_service.list_documents(db, user, filters, page, page_size)
    return Page(items=[DocumentOut.model_validate(x) for x in items], total=total, page=page, page_size=page_size)


@router.post("", response_model=DocumentOut, summary="在线创建文档（富文本/Markdown）")
def create_document(payload: DocumentCreate, request: Request, db: DbSession, user: CurrentUser):
    doc = doc_service.create_document(db, user, payload)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.CREATE.value,
           resource_type="document", resource_id=str(doc.id), meta=request_meta(request), commit=True)
    return DocumentOut.model_validate(doc)


@router.get("/download-local", summary="本地存储模式下的鉴权下载（须在 /{doc_id} 之前声明）")
def download_local(key: str, db: DbSession, user: CurrentUser):
    # 路径必须带 tenant 前缀，杜绝跨租户/目录穿越
    if not key.startswith(f"{user.tenant_id}/"):
        raise Forbidden("非法对象路径")
    return StreamingResponse(get_object_store().stream(key), media_type="application/octet-stream")


@router.get("/{doc_id}", response_model=DocumentOut, summary="文档详情")
def get_document(doc_id: uuid.UUID, db: DbSession, user: CurrentUser):
    doc = TenantRepository(db, user.tenant_id).get(Document, doc_id)
    PermissionService(db, user).check(
        Action.READ.value,
        Resource(tenant_id=str(user.tenant_id), kb_id=str(doc.kb_id),
                 department_id=str(doc.department_id) if doc.department_id else None,
                 owner_id=str(doc.owner_id) if doc.owner_id else None,
                 sensitivity=doc.sensitivity, visibility="dept", document_id=str(doc.id)),
    )
    return DocumentOut.model_validate(doc)


@router.patch("/{doc_id}", response_model=DocumentOut, summary="编辑文档（自动生成新版本并重索引）")
def patch_document(doc_id: uuid.UUID, payload: DocumentPatch, request: Request, db: DbSession, user: CurrentUser):
    doc = doc_service.patch_document(db, user, doc_id, payload)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.UPDATE.value,
           resource_type="document", resource_id=str(doc_id),
           detail={"version": doc.current_version, "summary": payload.change_summary},
           meta=request_meta(request), commit=True)
    return DocumentOut.model_validate(doc)


@router.delete("/{doc_id}", response_model=OkResponse, summary="删除文档（三方联动）")
def delete_document(doc_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    doc_service.soft_delete(db, user, doc_id)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.DELETE.value,
           resource_type="document", resource_id=str(doc_id), meta=request_meta(request), commit=True)
    return OkResponse(message="已软删除，向量与索引由异步任务清理")


@router.post("/{doc_id}/restore", response_model=OkResponse, summary="恢复已删除文档")
def restore_document(doc_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    doc_service.restore_document(db, user, doc_id)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="restore",
           resource_type="document", resource_id=str(doc_id), meta=request_meta(request), commit=True)
    return OkResponse(message="已恢复并重新入队索引")


@router.get("/{doc_id}/download", summary="获取原文件下载链接（鉴权后签名）")
def download_document(doc_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    url = doc_service.download_url(db, user, doc_id)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.DOWNLOAD.value,
           resource_type="document", resource_id=str(doc_id), meta=request_meta(request), commit=True)
    return {"url": url, "expires_in": 3600}


@router.post("/{doc_id}/reindex", response_model=OkResponse, summary="重新解析并重建向量")
def reindex_document(doc_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    doc = TenantRepository(db, user.tenant_id).get(Document, doc_id)
    PermissionService(db, user).check(
        Action.UPDATE.value,
        Resource(tenant_id=str(user.tenant_id), kb_id=str(doc.kb_id),
                 sensitivity=doc.sensitivity, visibility="tenant", document_id=str(doc.id)),
    )
    # 重建索引也是一次任务，必须有台账；否则 report_stage 会写回上一次的旧任务
    db.add(IngestJob(
        tenant_id=user.tenant_id, document_id=doc.id, queue="ingest",
        stage="queued", state="queued",
        idempotency_key=f"{user.tenant_id}:{doc.id}:reindex:{uuid.uuid4().hex[:8]}",
        max_retries=settings.INGEST_MAX_RETRIES,
    ))
    db.commit()
    if doc.source_uri:
        from app.tasks.ingest import enqueue_ingest

        enqueue_ingest(
            tenant_id=str(user.tenant_id), batch_id=None, document_id=str(doc.id),
            upload_id="reindex", kb_id=str(doc.kb_id), object_key=doc.source_uri,
            mime_type=doc.mime_type or "text/plain", is_large=False,
        )
    else:
        from app.tasks.ingest import enqueue_richtext_ingest

        enqueue_richtext_ingest(
            tenant_id=str(user.tenant_id), document_id=str(doc.id),
            kb_id=str(doc.kb_id), version_no=doc.current_version,
        )
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="reindex",
           resource_type="document", resource_id=str(doc_id), meta=request_meta(request), commit=True)
    return OkResponse(message="已重新入队")
