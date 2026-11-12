from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KbCreate(BaseModel):
    name: str
    description: str = ""
    department_id: uuid.UUID | None = None
    visibility: str = "dept"
    default_sensitivity: str = "internal"


class KbPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


class KbOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    department_id: uuid.UUID | None
    visibility: str
    embedding_model: str
    embedding_dim: int
    created_at: datetime

    model_config = {"from_attributes": True}


class CategoryCreate(BaseModel):
    name: str
    kb_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int = 0


class CategoryPatch(BaseModel):
    name: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None


class CategoryNode(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    level: int
    path_ids: list[str]
    sort_order: int
    children: list["CategoryNode"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str
    color: str = "#1677ff"


class TagOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str

    model_config = {"from_attributes": True}


class DocumentCreate(BaseModel):
    """在线创建（富文本/Markdown）：直接写 content_text，走同一套版本与索引流程。"""
    title: str
    kb_id: uuid.UUID
    category_id: uuid.UUID | None = None
    content_text: str = ""
    sensitivity: str = "internal"
    attributes: dict[str, Any] = Field(default_factory=dict)
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    change_summary: str = "创建文档"


class DocumentPatch(BaseModel):
    """任何编辑都会生成新版本，并把文档重新入队做增量索引。"""
    title: str | None = None
    content_text: str | None = None
    category_id: uuid.UUID | None = None
    sensitivity: str | None = None
    attributes: dict[str, Any] | None = None
    tag_ids: list[uuid.UUID] | None = None
    change_summary: str = ""


class DocumentOut(BaseModel):
    id: uuid.UUID
    kb_id: uuid.UUID
    category_id: uuid.UUID | None
    title: str
    doc_type: str
    mime_type: str | None
    size_bytes: int
    checksum: str | None
    sensitivity: str
    status: str
    current_version: int
    attributes: dict[str, Any]
    content_text: str | None = None
    tags: list[TagOut] = Field(default_factory=list)
    page_count: int
    chunk_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None

    model_config = {"from_attributes": True}


class VersionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version_no: int
    title: str
    checksum: str
    change_summary: str
    diff_stats: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionDetail(VersionOut):
    content_text: str


class DocumentListFilter(BaseModel):
    """文档列表与检索共用的筛选器；字段名与 apply_filters() 一一对应。"""
    query: str | None = None
    kb_ids: list[uuid.UUID] = Field(default_factory=list)
    category_ids: list[uuid.UUID] = Field(default_factory=list)
    include_descendants: bool = True
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    sensitivity_max: str | None = None
    status: str | None = None
    owner_id: uuid.UUID | None = None
    date_from: str | None = None
    date_to: str | None = None
    page: int = 1
    page_size: int = 20


class CompareRequest(BaseModel):
    from_version: int
    to_version: int
