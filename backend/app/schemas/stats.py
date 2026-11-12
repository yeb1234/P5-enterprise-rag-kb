"""看板响应结构。字段口径集中定义，避免三个页面各算一套。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OverviewOut(BaseModel):
    total_documents: int = 0
    indexed_documents: int = 0
    failed_documents: int = 0
    total_chunks: int = 0
    total_storage_bytes: int = 0
    knowledge_bases: int = 0
    categories: int = 0
    queries_today: int = 0
    active_users_today: int = 0
    ingest_success_rate: float = 0.0
    p95_query_latency_ms: int = 0
    pending_jobs: int = 0
    sensitivity_distribution: dict[str, int] = Field(default_factory=dict)
    status_distribution: dict[str, int] = Field(default_factory=dict)


class TrendPoint(BaseModel):
    date: str
    docs_created: int = 0
    docs_indexed: int = 0
    queries: int = 0
    upload_bytes: int = 0
    active_users: int = 0


class NamedCount(BaseModel):
    name: str
    value: int


class IngestStatsOut(BaseModel):
    total_jobs: int = 0
    succeeded: int = 0
    failed: int = 0
    running: int = 0
    avg_duration_ms: int = 0
    p95_duration_ms: int = 0
    by_stage: dict[str, int] = Field(default_factory=dict)
    by_queue: dict[str, int] = Field(default_factory=dict)


class ReportOut(BaseModel):
    overview: OverviewOut
    trend: list[TrendPoint] = Field(default_factory=list)
    by_department: list[NamedCount] = Field(default_factory=list)
    by_tag: list[NamedCount] = Field(default_factory=list)
    ingest: IngestStatsOut
    extra: dict[str, Any] = Field(default_factory=dict)
