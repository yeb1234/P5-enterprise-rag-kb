"""上传服务：批量会话 → 分片断点续传 → 服务端合并 → 秒传 → 入队。

为什么不用「一次 POST 传整个文件」：
  · 2GB 文件走 nginx/worker 内存中转，超时、OOM、断线重传整包都不可接受；
  · 分片后单请求恒定 8MiB，可并发、可续传、可校验、可限流；
  · 合并发生在对象存储服务端（compose_object），API 不碰整文件字节。
所有状态变化都写 PG，Redis 只做实时通知，因此刷新页面不会丢进度。
"""
from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ALLOWED_MIME_PREFIXES, Action, BatchStatus, DocStatus, UploadStatus
from app.core.errors import BadRequest, Conflict, Forbidden, NotFound, PayloadTooLarge
from app.integrations.object_storage import get_object_store, object_key
from app.models.knowledge import Document, DocumentVersion, KnowledgeBase, Tag
from app.models.pipeline import BatchUpload, IngestJob, UploadChunk, UploadSession
from app.schemas.upload import (
    BatchInitOut,
    BatchInitRequest,
    ChunkAck,
    UploadCompleteOut,
    UploadInitOut,
    UploadInitRequest,
    UploadStatusOut,
)
from app.services.progress import build_snapshot, publish_progress, publish_upload_chunk_progress

if TYPE_CHECKING:
    from app.core.deps import Principal

# 明确拒绝可执行/宏文件，文件上传是企业内网最常见的入口攻击面
BLOCKED_EXTENSIONS = {
    ".exe", ".dll", ".so", ".bat", ".cmd", ".ps1", ".sh", ".js", ".vbs",
    ".docm", ".xlsm", ".pptm", ".hta", ".scr", ".jar",
}


def _validate(filename: str, size_bytes: int, mime: str) -> None:
    lower = filename.lower()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    if ext in BLOCKED_EXTENSIONS:
        raise BadRequest(f"不允许上传 {ext} 文件（可执行/宏风险）")
    if size_bytes > settings.max_upload_size_bytes:
        raise PayloadTooLarge(f"单文件上限 {settings.MAX_UPLOAD_SIZE_MB}MB")
    if not any(mime.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        # MIME 由客户端提供，只是第一道；合并后还会做魔数校验
        raise BadRequest(f"不支持的文件类型：{mime}")


def _check_magic(head: bytes, mime: str) -> bool:
    """魔数双校验：PDF 必须 %PDF 开头，Office(OOXML) 必须是 ZIP。"""
    if "pdf" in mime:
        return head.startswith(b"%PDF")
    if "officedocument" in mime:
        return head.startswith(b"PK\x03\x04")
    return True  # 文本/Markdown 等无魔数


def _kb_resource(kb: KnowledgeBase):
    from app.services.permission import Resource

    return Resource(
        tenant_id=str(kb.tenant_id), kb_id=str(kb.id),
        department_id=str(kb.department_id) if kb.department_id else None,
        visibility=kb.visibility, sensitivity=kb.default_sensitivity,
    )


def init_batch(db: Session, user: "Principal", payload: BatchInitRequest) -> BatchInitOut:
    from app.services.permission import PermissionService

    if payload.total_files > settings.MAX_BATCH_FILES:
        raise BadRequest(f"单批次最多 {settings.MAX_BATCH_FILES} 个文件")
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == payload.kb_id,
        KnowledgeBase.tenant_id == user.tenant_id,
        KnowledgeBase.is_deleted.is_(False),
    ).first()
    if kb is None:
        raise NotFound("知识库不存在")
    PermissionService(db, user).check(Action.UPLOAD.value, _kb_resource(kb))

    batch = BatchUpload(
        tenant_id=user.tenant_id, user_id=user.id, kb_id=kb.id,
        total_files=payload.total_files, total_bytes=payload.total_bytes,
        status="collecting",
    )
    db.add(batch)
    db.commit()
    return BatchInitOut(batch_id=batch.id, status=batch.status, created_at=batch.created_at)


def init_upload(db: Session, user: "Principal", payload: UploadInitRequest) -> UploadInitOut:
    from app.services.permission import PermissionService

    _validate(payload.filename, payload.size_bytes, payload.mime_type)
    batch = db.query(BatchUpload).filter(
        BatchUpload.id == payload.batch_id, BatchUpload.tenant_id == user.tenant_id
    ).first()
    if batch is None:
        raise NotFound("批次不存在")
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == payload.kb_id,
        KnowledgeBase.tenant_id == user.tenant_id,
        KnowledgeBase.is_deleted.is_(False),
    ).first()
    if kb is None:
        raise NotFound("知识库不存在")
    PermissionService(db, user).check(Action.UPLOAD.value, _kb_resource(kb))

    # 租户配额：防止一个部门把磁盘打满
    used = db.query(Document.size_bytes).filter(
        Document.tenant_id == user.tenant_id, Document.is_deleted.is_(False)
    ).all()
    if sum(int(r[0] or 0) for r in used) + payload.size_bytes > _tenant_quota(db, user.tenant_id):
        raise PayloadTooLarge("租户存储配额不足")

    chunk_size = settings.upload_chunk_size_bytes
    total_chunks = max(1, math.ceil(payload.size_bytes / chunk_size))
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.UPLOAD_SESSION_TTL_HOURS)

    # ---------- 秒传：同租户同 checksum 且已索引的文件直接复用 ----------
    duplicate = (
        db.query(Document)
        .filter(
            Document.tenant_id == user.tenant_id,
            Document.checksum == payload.checksum,
            Document.status == DocStatus.INDEXED.value,
            Document.is_deleted.is_(False),
        )
        .first()
    )
    if duplicate:
        session = UploadSession(
            tenant_id=user.tenant_id, batch_id=batch.id, user_id=user.id, kb_id=kb.id,
            category_id=payload.category_id, filename=payload.filename,
            mime_type=payload.mime_type, size_bytes=payload.size_bytes, checksum=payload.checksum,
            chunk_size=chunk_size, total_chunks=total_chunks, received_chunks=total_chunks,
            object_key=duplicate.source_uri or "", status=UploadStatus.INSTANT.value,
            document_id=duplicate.id, meta=_meta(payload), expires_at=expires_at,
        )
        db.add(session)
        batch.succeeded += 1
        db.commit()
        publish_progress(
            str(user.tenant_id), str(batch.id),
            {"upload_id": str(session.id), "document_id": str(duplicate.id),
             "filename": payload.filename, "size_bytes": payload.size_bytes,
             "status": "succeeded", "phase": "done", "stage": "done", "progress": 100.0,
             "message": "秒传命中，复用已有解析结果"},
            event_type="done",
        )
        return UploadInitOut(
            upload_id=session.id, batch_id=batch.id, document_id=duplicate.id,
            status=session.status, instant=True, chunk_size=chunk_size,
            total_chunks=total_chunks, received_chunks=list(range(total_chunks)),
            expires_at=expires_at,
        )

    object_key_final = object_key(str(user.tenant_id), "documents", str(uuid.uuid4()), payload.filename)
    session = UploadSession(
        tenant_id=user.tenant_id, batch_id=batch.id, user_id=user.id, kb_id=kb.id,
        category_id=payload.category_id, filename=payload.filename, mime_type=payload.mime_type,
        size_bytes=payload.size_bytes, checksum=payload.checksum, chunk_size=chunk_size,
        total_chunks=total_chunks, object_key=object_key_final,
        status=UploadStatus.INITIALIZED.value, meta=_meta(payload), expires_at=expires_at,
    )
    db.add(session)
    db.commit()

    return UploadInitOut(
        upload_id=session.id, batch_id=batch.id, document_id=None,
        status=session.status, instant=False, chunk_size=chunk_size,
        total_chunks=total_chunks, received_chunks=[], expires_at=expires_at,
    )


def _meta(payload: UploadInitRequest) -> dict:
    return {
        "tags": payload.tags, "attributes": payload.attributes,
        "sensitivity": payload.sensitivity, "title": payload.title,
    }


def _tenant_quota(db: Session, tenant_id: uuid.UUID) -> int:
    from app.models.identity import Tenant

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return int(tenant.quota_bytes) if tenant else 50 * 1024 * 1024 * 1024


def _get_session(db: Session, user: "Principal", upload_id: uuid.UUID) -> UploadSession:
    s = db.query(UploadSession).filter(
        UploadSession.id == upload_id, UploadSession.tenant_id == user.tenant_id
    ).first()
    if s is None:
        raise NotFound("上传会话不存在")
    if s.user_id != user.id and not user.is_admin:
        raise Forbidden("只能操作自己发起的上传")
    return s


def put_chunk(
    db: Session, user: "Principal", upload_id: uuid.UUID, idx: int, data: bytes
) -> ChunkAck:
    s = _get_session(db, user, upload_id)
    if s.status in (UploadStatus.COMPLETED.value, UploadStatus.CANCELLED.value, UploadStatus.INSTANT.value):
        raise Conflict("上传会话已结束")
    if not (0 <= idx < s.total_chunks):
        raise BadRequest("分片序号越界")
    if len(data) > settings.upload_chunk_size_bytes + 1024:
        raise PayloadTooLarge("分片超过配置大小")
    if idx == 0 and not _check_magic(data[:8], s.mime_type):
        raise BadRequest("文件内容与声明类型不符（魔数校验失败）")

    # 幂等：同一分片重复上传直接返回成功，不产生第二条记录
    existing = db.query(UploadChunk).filter(
        UploadChunk.upload_id == s.id, UploadChunk.idx == idx
    ).first()
    if existing is None:
        part_key = object_key(str(user.tenant_id), "parts", str(s.id), f"{idx:06d}.part")
        store = get_object_store()
        store.put(part_key, data, content_type="application/octet-stream")
        db.add(UploadChunk(
            tenant_id=user.tenant_id, upload_id=s.id, idx=idx,
            size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest(),
            object_key=part_key,
        ))
        s.received_chunks += 1
        s.status = UploadStatus.UPLOADING.value
        db.commit()
    else:
        part_key = existing.object_key

    received = db.query(UploadChunk).filter(UploadChunk.upload_id == s.id).count()
    fraction = received / s.total_chunks if s.total_chunks else 0
    if s.batch_id:
        publish_upload_chunk_progress(
            str(user.tenant_id), str(s.batch_id), str(s.id), s.filename,
            received, s.total_chunks, s.size_bytes,
        )
    return ChunkAck(upload_id=s.id, idx=idx, received=received, total=s.total_chunks,
                    upload_progress=round(fraction, 4))


def upload_status(db: Session, user: "Principal", upload_id: uuid.UUID) -> UploadStatusOut:
    s = _get_session(db, user, upload_id)
    rows = db.query(UploadChunk.idx).filter(UploadChunk.upload_id == s.id).all()
    received = sorted(int(r[0]) for r in rows)
    missing = [i for i in range(s.total_chunks) if i not in set(received)]
    return UploadStatusOut(
        upload_id=s.id, status=s.status, total_chunks=s.total_chunks,
        received_chunks=received, missing_chunks=missing,
        upload_progress=round(len(received) / s.total_chunks, 4) if s.total_chunks else 0,
        expires_at=s.expires_at,
    )


def complete_upload(db: Session, user: "Principal", upload_id: uuid.UUID) -> UploadCompleteOut:
    """合并 + 校验 + 建文档 + 建版本 + 建任务台账 + 投递 Celery 链。"""
    s = _get_session(db, user, upload_id)
    if s.status == UploadStatus.COMPLETED.value:
        job = db.query(IngestJob).filter(IngestJob.upload_id == s.id).first()
        return UploadCompleteOut(
            upload_id=s.id, document_id=s.document_id, job_id=job.id if job else None,
            celery_task_id=job.celery_task_id if job else None,
            status=s.status, queue=job.queue if job else "ingest", message="已完成（幂等返回）",
        )
    parts = (
        db.query(UploadChunk)
        .filter(UploadChunk.upload_id == s.id)
        .order_by(UploadChunk.idx.asc())
        .all()
    )
    if len(parts) != s.total_chunks:
        raise Conflict(f"分片未齐：{len(parts)}/{s.total_chunks}，请按 missing_chunks 续传")
    if sum(p.size_bytes for p in parts) != s.size_bytes:
        raise BadRequest("分片总长度与声明文件大小不一致")

    s.status = UploadStatus.ASSEMBLING.value
    db.commit()

    store = get_object_store()
    store.compose([p.object_key for p in parts], s.object_key, s.mime_type)

    # 全文件 SHA256：流式分块哈希，2GB 文件也不会把内存打爆。
    # 浏览器对超大文件无法低内存算整文件哈希，客户端可传 "skip:<size>"：
    # 此时服务端合并后的哈希即为权威 checksum（大文件不参与秒传，但校验强度不降）。
    digest = hashlib.sha256()
    for block in store.stream(s.object_key):
        digest.update(block)
    computed = digest.hexdigest()
    if s.checksum.startswith("skip:"):
        s.checksum = computed
    elif computed != s.checksum:
        s.status = UploadStatus.FAILED.value
        s.error_message = "合并后校验和不匹配，请重新上传"
        db.commit()
        raise BadRequest(s.error_message)

    meta = s.meta or {}
    tags = _resolve_tags(db, user.tenant_id, meta.get("tags", []))
    doc = Document(
        tenant_id=user.tenant_id, kb_id=s.kb_id, category_id=s.category_id,
        department_id=user.department_id, title=meta.get("title") or s.filename,
        doc_type="file", source_uri=s.object_key, mime_type=s.mime_type,
        size_bytes=s.size_bytes, checksum=s.checksum,
        sensitivity=meta.get("sensitivity", "internal"),
        attributes=meta.get("attributes", {}), status=DocStatus.PENDING.value,
        current_version=1, owner_id=user.id, created_by=user.id, updated_by=user.id,
        tags=tags,
    )
    db.add(doc)
    db.flush()

    db.add(DocumentVersion(
        tenant_id=user.tenant_id, document_id=doc.id, version_no=1, title=doc.title,
        content_text="", content_uri=s.object_key, checksum=s.checksum,
        change_summary="初始上传", diff_stats={"added": 0, "removed": 0},
        created_by=user.id,
    ))

    is_large = s.size_bytes >= settings.LARGE_FILE_SIZE_MB * 1024 * 1024
    queue = "ingest_heavy" if is_large else "ingest"
    job = IngestJob(
        tenant_id=user.tenant_id, batch_id=s.batch_id, upload_id=s.id, document_id=doc.id,
        queue=queue, stage="queued", state="queued", progress=0.0,
        idempotency_key=f"{user.tenant_id}:{s.id}:v1", is_large=is_large,
        max_retries=settings.INGEST_MAX_RETRIES,
    )
    db.add(job)
    s.document_id = doc.id
    s.status = UploadStatus.COMPLETED.value
    db.commit()

    # 入队放在事务提交之后：避免 worker 抢先跑、却读不到刚创建的文档
    from app.tasks.ingest import enqueue_ingest

    result = enqueue_ingest(
        tenant_id=str(user.tenant_id),
        batch_id=str(s.batch_id) if s.batch_id else None,
        document_id=str(doc.id), upload_id=str(s.id), kb_id=str(s.kb_id),
        object_key=s.object_key, mime_type=s.mime_type, is_large=is_large,
    )
    job.celery_task_id = result.id if hasattr(result, "id") else str(result)
    db.commit()

    if s.batch_id:
        snap = build_snapshot(db, str(user.tenant_id), str(s.batch_id))
        publish_progress(
            str(user.tenant_id), str(s.batch_id),
            {"upload_id": str(s.id), "document_id": str(doc.id), "filename": s.filename,
             "size_bytes": s.size_bytes, "status": "queued", "phase": "ingesting",
             "stage": "queued", "progress": 0.0, "message": "已入队，等待解析"},
        )
        batch = db.query(BatchUpload).filter(BatchUpload.id == s.batch_id).first()
        if batch:
            batch.status = BatchStatus.PROCESSING.value
            batch.progress = snap.progress
            db.commit()

    return UploadCompleteOut(
        upload_id=s.id, document_id=doc.id, job_id=job.id,
        celery_task_id=job.celery_task_id, status=s.status, queue=queue,
        message="上传完成，已进入异步解析队列",
    )


def _resolve_tags(db: Session, tenant_id: uuid.UUID, names: list[str]) -> list[Tag]:
    result = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        tag = db.query(Tag).filter(Tag.tenant_id == tenant_id, Tag.name == name).first()
        if tag is None:
            tag = Tag(tenant_id=tenant_id, name=name)
            db.add(tag)
            db.flush()
        result.append(tag)
    return result


def cancel_upload(db: Session, user: "Principal", upload_id: uuid.UUID) -> None:
    s = _get_session(db, user, upload_id)
    if s.status in (UploadStatus.COMPLETED.value, UploadStatus.INSTANT.value):
        raise Conflict("已完成的上传不能取消，请走文档删除流程")
    parts = db.query(UploadChunk).filter(UploadChunk.upload_id == s.id).all()
    if parts:
        try:
            get_object_store().remove([p.object_key for p in parts])
        except Exception:
            pass
        for p in parts:
            db.delete(p)
    s.status = UploadStatus.CANCELLED.value
    db.commit()
