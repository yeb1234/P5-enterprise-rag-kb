"""文档资源服务：CRUD、多级分类/标签/属性筛选、软删除三方联动、重新索引。"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Query, Session

from app.core.constants import ROLE_SENSITIVITY_CEILING, SENSITIVITY_LEVEL, Action, DocStatus
from app.core.errors import BadRequest, NotFound
from app.models.knowledge import Category, Document, DocumentVersion, KnowledgeBase, Tag, document_tags
from app.schemas.knowledge import DocumentCreate, DocumentPatch
from app.services import version as version_service
from app.services.permission import PermissionService, Resource

if TYPE_CHECKING:
    from app.core.deps import Principal

CJK = re.compile(r"[\u4e00-\u9fff]")


def apply_filters(q: Query, user: "Principal", filters) -> Query:
    """文档列表与检索共用同一套过滤条件，保证「搜得到 = 看得到」。"""
    q = q.filter(Document.tenant_id == user.tenant_id, Document.is_deleted.is_(False))

    # 部门 + 敏感度：非管理员强制收窄
    if not user.is_admin:
        ceiling = ROLE_SENSITIVITY_CEILING.get(user.role, 0)
        allowed = [k for k, v in SENSITIVITY_LEVEL.items() if v <= ceiling]
        q = q.filter(
            or_(Document.department_id == user.department_id, Document.department_id.is_(None)),
            Document.sensitivity.in_(allowed),
        )

    if filters.kb_ids:
        q = q.filter(Document.kb_id.in_(filters.kb_ids))
    if filters.category_ids:
        cat_ids = [str(c) for c in filters.category_ids]
        if filters.include_descendants:
            # path_ids 是祖先数组，@> 一次命中整棵子树，走 GIN 索引
            subtree = select(Category.id).where(
                or_(Category.id.in_(filters.category_ids), Category.path_ids.contains(cat_ids))
            ).scalar_subquery()
            q = q.filter(Document.category_id.in_(subtree))
        else:
            q = q.filter(Document.category_id.in_(filters.category_ids))
    for tag_id in filters.tag_ids:
        q = q.filter(
            Document.id.in_(
                select(document_tags.c.document_id).where(document_tags.c.tag_id == tag_id)
            )
        )
    # JSONB 自定义属性：用 @> 走 GIN 索引；key 由前端给，但值走参数绑定，无注入风险
    for key, value in (filters.attributes or {}).items():
        q = q.filter(Document.attributes.contains({key: value}))
    if filters.sensitivity_max:
        ceiling = SENSITIVITY_LEVEL.get(filters.sensitivity_max, 1)
        q = q.filter(
            Document.sensitivity.in_([k for k, v in SENSITIVITY_LEVEL.items() if v <= ceiling])
        )
    if filters.status:
        q = q.filter(Document.status == filters.status)
    if filters.owner_id:
        q = q.filter(Document.owner_id == filters.owner_id)
    if filters.date_from:
        q = q.filter(Document.created_at >= filters.date_from)
    if filters.date_to:
        q = q.filter(Document.created_at <= filters.date_to)
    return q


def apply_fulltext(q: Query, query: str) -> Query:
    """PostgreSQL 全文检索：英文用词位查询；中文走 pg_trgm 模糊匹配兜底。

    生产要真正的中文分词，装 zhparser 后把 'simple' 换成 'zhcfg'，
    并给 search_vector 的触发器改成对应 regconfig（migration 0001 里有注释）。
    """
    text = (query or "").strip()
    if not text:
        return q
    if CJK.search(text):
        return q.filter(
            or_(
                Document.title.ilike(f"%{text}%"),
                Document.content_text.ilike(f"%{text}%"),
            )
        )
    tsq = func.websearch_to_tsquery("simple", text)
    return q.filter(
        or_(
            Document.search_vector.op("@@")(tsq),
            Document.title.ilike(f"%{text}%"),
        )
    )


def list_documents(
    db: Session, user: "Principal", filters, page: int, page_size: int
) -> tuple[list[Document], int]:
    q = db.query(Document)
    q = apply_filters(q, user, filters)
    if getattr(filters, "query", None):
        q = apply_fulltext(q, filters.query)
    total = q.order_by(None).count()
    rows = (
        q.order_by(Document.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(min(page_size, 200))
        .all()
    )
    return rows, total


def _check_kb(db: Session, user: "Principal", kb_id: uuid.UUID, action: str) -> KnowledgeBase:
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == user.tenant_id,
        KnowledgeBase.is_deleted.is_(False),
    ).first()
    if kb is None:
        raise NotFound("知识库不存在")
    PermissionService(db, user).check(
        action,
        Resource(
            tenant_id=str(user.tenant_id), kb_id=str(kb.id),
            department_id=str(kb.department_id) if kb.department_id else None,
            visibility=kb.visibility, sensitivity=kb.default_sensitivity,
        ),
    )
    return kb


def create_document(db: Session, user: "Principal", payload: DocumentCreate) -> Document:
    kb = _check_kb(db, user, payload.kb_id, Action.CREATE.value)
    tags = [db.query(Tag).filter(Tag.id == t, Tag.tenant_id == user.tenant_id).first()
            for t in payload.tag_ids]
    doc = Document(
        tenant_id=user.tenant_id, kb_id=kb.id, category_id=payload.category_id,
        department_id=user.department_id, title=payload.title, doc_type="richtext",
        content_text=payload.content_text, sensitivity=payload.sensitivity,
        attributes=payload.attributes, status=DocStatus.PENDING.value,
        current_version=1, owner_id=user.id, created_by=user.id, updated_by=user.id,
        checksum=version_service._checksum(payload.content_text),
        size_bytes=len(payload.content_text.encode("utf-8")),
        tags=[t for t in tags if t],
    )
    db.add(doc)
    db.flush()
    version_service.snapshot(db, doc, payload.change_summary or "创建文档", user.id)
    from app.tasks.ingest import enqueue_richtext_ingest

    result = enqueue_richtext_ingest(
        tenant_id=str(user.tenant_id), document_id=str(doc.id), kb_id=str(kb.id),
    )
    db.commit()
    _attach_job_id(db, doc.id, result)
    return doc


def _attach_job_id(db: Session, doc_id: uuid.UUID, result) -> None:
    from app.models.pipeline import IngestJob

    job = (
        db.query(IngestJob).filter(IngestJob.document_id == doc_id)
        .order_by(IngestJob.created_at.desc()).first()
    )
    if job and hasattr(result, "id"):
        job.celery_task_id = result.id
        db.commit()


def patch_document(db: Session, user: "Principal", doc_id: uuid.UUID, payload: DocumentPatch) -> Document:
    doc = db.query(Document).filter(
        Document.id == doc_id, Document.tenant_id == user.tenant_id, Document.is_deleted.is_(False)
    ).first()
    if doc is None:
        raise NotFound("文档不存在")
    PermissionService(db, user).check(
        Action.UPDATE.value,
        Resource(tenant_id=str(user.tenant_id), kb_id=str(doc.kb_id),
                 department_id=str(doc.department_id) if doc.department_id else None,
                 owner_id=str(doc.owner_id) if doc.owner_id else None,
                 sensitivity=doc.sensitivity, visibility="dept", document_id=str(doc.id)),
    )

    changes = payload.model_dump(exclude_unset=True)
    tag_ids = changes.pop("tag_ids", None)
    summary = changes.pop("change_summary", "") or "编辑文档"
    for key, value in changes.items():
        if value is not None:
            setattr(doc, key, value)
    if tag_ids is not None:
        doc.tags = [t for t in (
            db.query(Tag).filter(Tag.id == t, Tag.tenant_id == user.tenant_id).first()
            for t in tag_ids
        ) if t]
    doc.updated_by = user.id
    doc.current_version += 1
    doc.status = DocStatus.PENDING.value   # 编辑后必须重建索引，旧版本向量才不会残留
    doc.checksum = version_service._checksum(doc.content_text)
    version_service.snapshot(db, doc, summary, user.id)

    from app.tasks.ingest import enqueue_richtext_ingest

    result = enqueue_richtext_ingest(
        tenant_id=str(user.tenant_id), document_id=str(doc.id), kb_id=str(doc.kb_id),
        version_no=doc.current_version,
    )
    db.commit()
    _attach_job_id(db, doc.id, result)
    return doc


def soft_delete(db: Session, user: "Principal", doc_id: uuid.UUID) -> None:
    """删除 = PG 软删 + Milvus 向量清理 + 对象存储保留（可恢复/可审计）。

    顺序固定：先 PG 标记，再异步清向量。反过来会留下「向量还在但元数据已删」的幽灵片段。
    """
    doc = db.query(Document).filter(
        Document.id == doc_id, Document.tenant_id == user.tenant_id, Document.is_deleted.is_(False)
    ).first()
    if doc is None:
        raise NotFound("文档不存在")
    PermissionService(db, user).check(
        Action.DELETE.value,
        Resource(tenant_id=str(user.tenant_id), kb_id=str(doc.kb_id),
                 department_id=str(doc.department_id) if doc.department_id else None,
                 owner_id=str(doc.owner_id) if doc.owner_id else None,
                 sensitivity=doc.sensitivity, visibility="dept", document_id=str(doc.id)),
    )
    doc.is_deleted = True
    doc.deleted_at = datetime.now(timezone.utc)
    doc.status = DocStatus.DELETED.value
    db.commit()

    from app.tasks.ingest import cleanup_document

    cleanup_document.delay(str(user.tenant_id), str(doc.id), str(doc.kb_id))


def restore_document(db: Session, user: "Principal", doc_id: uuid.UUID) -> None:
    doc = db.query(Document).filter(
        Document.id == doc_id, Document.tenant_id == user.tenant_id, Document.is_deleted.is_(True)
    ).first()
    if doc is None:
        raise NotFound("已删除文档不存在")
    PermissionService(db, user).check(Action.MANAGE.value, Resource(
        tenant_id=str(user.tenant_id), kb_id=str(doc.kb_id), visibility="tenant"))
    doc.is_deleted = False
    doc.deleted_at = None
    doc.status = DocStatus.PENDING.value
    db.commit()
    from app.tasks.ingest import enqueue_richtext_ingest

    enqueue_richtext_ingest(
        tenant_id=str(user.tenant_id), document_id=str(doc.id), kb_id=str(doc.kb_id),
        version_no=doc.current_version,
    )


def download_url(db: Session, user: "Principal", doc_id: uuid.UUID) -> str:
    from app.integrations.object_storage import get_object_store

    doc = db.query(Document).filter(
        Document.id == doc_id, Document.tenant_id == user.tenant_id, Document.is_deleted.is_(False)
    ).first()
    if doc is None or not doc.source_uri:
        raise NotFound("文档或原文件不存在")
    PermissionService(db, user).check(
        Action.DOWNLOAD.value,
        Resource(tenant_id=str(user.tenant_id), kb_id=str(doc.kb_id),
                 department_id=str(doc.department_id) if doc.department_id else None,
                 owner_id=str(doc.owner_id) if doc.owner_id else None,
                 sensitivity=doc.sensitivity, visibility="dept", document_id=str(doc.id)),
    )
    return get_object_store().presigned_url(doc.source_uri)
