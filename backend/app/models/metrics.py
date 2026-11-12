"""可观测与报表数据源。指标明细一条请求一行，日报由 Celery beat 预聚合。"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class MetricEvent(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "metric_events"
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieved_n: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(default=False, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class DailyStat(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """看板读这张预聚合表，不扫明细；每天由 beat 重算一次。"""
    __tablename__ = "daily_stats"
    stat_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    docs_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    docs_indexed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    docs_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    upload_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    queries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ingest_success_rate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 百分比 * 100
    p95_latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # 按部门/标签的分布

    __table_args__ = (UniqueConstraint("tenant_id", "stat_date", name="uq_daily_stat"),)
