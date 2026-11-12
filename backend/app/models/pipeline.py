"""上传与异步流水线模型：批次 → 上传会话 → 分片 → 入库任务。

进度真值在 PG（IngestJob + BatchUpload），Redis 只做实时通道与节流缓存：
worker 崩了、SSE 断了、浏览器刷新了，都能从 PG 快照恢复，不会「进度归零」。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class BatchUpload(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """一次多文档批量上传的聚合视图。"""
    __tablename__ = "batch_uploads"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kb_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancelled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="collecting", nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UploadSession(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """一个文件的分片上传会话，支持断点续传与秒传。"""
    __tablename__ = "upload_sessions"
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("batch_uploads.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kb_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    received_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="initialized", nullable=False, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # tags / attributes / sensitivity
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_upload_tenant_checksum", "tenant_id", "checksum", "status"),)


class UploadChunk(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "upload_chunks"
    upload_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("upload_sessions.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("upload_id", "idx", name="uq_upload_chunk_idx"),)


class IngestJob(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """入库任务台账。Celery result 只是辅助，本表才是状态真值。"""
    __tablename__ = "ingest_jobs"
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    upload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    queue: Mapped[str] = mapped_column(String(32), default="ingest", nullable=False)

    stage: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stage_progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    is_large: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (Index("ix_job_tenant_state", "tenant_id", "state", "heartbeat_at"),)
