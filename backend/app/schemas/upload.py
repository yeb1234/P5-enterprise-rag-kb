"""上传契约：批量 → 单文件分片 → 合并 → 进度快照。

进度是层级结构：batch(批次) → file(文件) → stage(阶段) → unit(页/片段)。
前端只订阅 batch 一条 SSE 流，就能渲染整批所有文件的进度。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BatchInitRequest(BaseModel):
    kb_id: uuid.UUID
    total_files: int = Field(ge=1, le=200)
    total_bytes: int = Field(ge=0)


class BatchInitOut(BaseModel):
    batch_id: uuid.UUID
    status: str
    created_at: datetime


class UploadInitRequest(BaseModel):
    batch_id: uuid.UUID
    filename: str
    size_bytes: int = Field(gt=0)
    checksum: str = Field(min_length=16, max_length=64)  # 客户端算好的整文件 SHA256
    mime_type: str = "application/octet-stream"
    kb_id: uuid.UUID
    category_id: uuid.UUID | None = None
    sensitivity: str = "internal"
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None


class UploadInitOut(BaseModel):
    upload_id: uuid.UUID
    batch_id: uuid.UUID
    document_id: uuid.UUID | None = None
    status: str
    instant: bool = False                 # 秒传命中
    chunk_size: int
    total_chunks: int
    received_chunks: list[int] = Field(default_factory=list)  # 断点续传：已收到的分片号
    expires_at: datetime


class ChunkAck(BaseModel):
    upload_id: uuid.UUID
    idx: int
    received: int
    total: int
    upload_progress: float               # 0~1，仅文件传输阶段


class UploadStatusOut(BaseModel):
    upload_id: uuid.UUID
    status: str
    total_chunks: int
    received_chunks: list[int]
    missing_chunks: list[int]
    upload_progress: float
    expires_at: datetime


class UploadCompleteOut(BaseModel):
    upload_id: uuid.UUID
    document_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    celery_task_id: str | None
    status: str
    queue: str
    message: str


class FileProgress(BaseModel):
    upload_id: str | None = None
    document_id: str | None = None
    filename: str
    size_bytes: int = 0
    status: str
    phase: str = "uploading"             # uploading | ingesting | done | failed | cancelled
    stage: str = "queued"
    progress: float = 0.0                # 0~100
    stage_progress: float = 0.0
    done_units: int = 0
    total_units: int = 0
    message: str = ""
    retry_count: int = 0
    error: str | None = None
    updated_at: str | None = None


class BatchProgress(BaseModel):
    batch_id: str
    status: str
    progress: float = 0.0                # 按文件体积加权
    total_files: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    running: int = 0
    files: list[FileProgress] = Field(default_factory=list)
    generated_at: str
    seq: int = 0                         # SSE Last-Event-ID 用


class JobOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID | None
    document_id: uuid.UUID
    stage: str
    state: str
    progress: float
    message: str
    retry_count: int
    error: str | None
    queue: str
    is_large: bool
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}
