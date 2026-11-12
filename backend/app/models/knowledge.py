"""知识资产模型：知识库、多级分类、标签、文档、版本、切片。

PG 只存文本与元数据；向量不落 PG，全部交给 Milvus，以 chunk.id 作幂等键。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPkMixin

document_tags = Table(
    "document_tags",
    Base.metadata,
    # 关联表只放两个外键：租户隔离通过 document_id / tag_id 指向的主表保证。
    # 若在这里加 tenant_id NOT NULL，SQLAlchemy 的 secondary 写入不会自动填它，INSERT 必然失败。
    Column("document_id", Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class KnowledgeBase(UUIDPkMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "knowledge_bases"
    department_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default="dept", nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(64), default="bge-m3", nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    default_sensitivity: Mapped[str] = mapped_column(String(16), default="internal", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class Category(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """多级分类。path_ids 是祖先 id 数组，用 GIN 索引做「含子孙」筛选；
    这比递归 CTE 便宜，代价是移动分类时要批量更新后代的 path_ids（服务层已封装）。"""
    __tablename__ = "categories"
    kb_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("categories.id"), nullable=True
    )
    path_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_category_path_ids", "path_ids", postgresql_using="gin"),
        Index("ix_category_kb", "tenant_id", "kb_id", "parent_id"),
        UniqueConstraint("tenant_id", "kb_id", "parent_id", "name", name="uq_category_name"),
    )


class Tag(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#1677ff", nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tag_name"),)


class Document(UUIDPkMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "documents"
    kb_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), default="file", nullable=False)  # file | richtext
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)     # MinIO object key
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    sensitivity: Mapped[str] = mapped_column(String(16), default="internal", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # 自定义属性，GIN 索引

    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 在线编辑正文 / 解析摘要
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    # 触发器维护的全文向量（见 migration 0001）；中文另有 pg_trgm 兜底
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    tags: Mapped[list[Tag]] = relationship(secondary=document_tags, lazy="selectin")

    __table_args__ = (
        Index("ix_documents_tenant_kb_status", "tenant_id", "kb_id", "status"),
        Index("ix_documents_dept", "tenant_id", "department_id"),
        Index("ix_documents_category", "tenant_id", "category_id"),
        Index("ix_documents_attributes", "attributes", postgresql_using="gin"),
        Index("ix_documents_fts", "search_vector", postgresql_using="gin"),
        Index("ix_documents_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
        Index("ix_documents_checksum", "tenant_id", "checksum"),
    )


class DocumentVersion(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """每次编辑生成一条不可变快照；版本对比基于 content_text 的行级 diff。"""
    __tablename__ = "document_versions"
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_uri: Mapped[str | None] = mapped_column(Text, nullable=True)  # 大正文/原文件快照
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    diff_stats: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_doc_version"),)


class Chunk(UUIDPkMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """检索最小单元。acl 字段是逐片段权限的预留位，当前不参与主链路判定。"""
    __tablename__ = "chunks"
    kb_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(16), default="internal", nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)  # 父子块
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), default="paragraph", nullable=False)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_level: Mapped[str] = mapped_column(String(8), default="L1", nullable=False)  # L1/L2/L3
    acl: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # 预留：段落级 ACL 条件
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    __table_args__ = (
        UniqueConstraint("document_id", "version_no", "seq", name="uq_chunk_seq"),
        Index("ix_chunks_fts", "search_vector", postgresql_using="gin"),
        Index("ix_chunks_doc", "tenant_id", "document_id", "is_deleted"),
    )
