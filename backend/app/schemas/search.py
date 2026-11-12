from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    kb_ids: list[uuid.UUID] = Field(default_factory=list)
    category_ids: list[uuid.UUID] = Field(default_factory=list)
    include_descendants: bool = True       # 多级分类：是否含子孙分类
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)  # JSONB 等值/包含筛选
    sensitivity_max: str | None = None
    status: str = "indexed"
    owner_id: uuid.UUID | None = None
    date_from: str | None = None
    date_to: str | None = None


class SearchRequest(BaseModel):
    query: str
    mode: str = "hybrid"                   # hybrid | keyword | semantic
    top_k: int = 20
    page: int = 1
    page_size: int = 20
    filters: SearchFilters = Field(default_factory=SearchFilters)
    enable_answer: bool = False            # 为 true 时走 Agent 编排生成带引用的答案


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    page_no: int | None = None
    section_path: str = ""
    snippet: str
    score: float = 0.0
    source: str = "hybrid"                 # keyword | semantic | hybrid


class SearchHit(BaseModel):
    document_id: str
    title: str
    category_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    score: float
    matched_by: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    page: int
    page_size: int
    took_ms: int
    cache_hit: bool = False
    hits: list[SearchHit]
    answer: str | None = None
    trace_id: str | None = None


class AnswerRequest(BaseModel):
    query: str
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = 8
