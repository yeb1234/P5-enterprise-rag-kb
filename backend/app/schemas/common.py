"""通用响应契约。所有列表接口统一 {items, total, page, page_size}。"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20


class OkResponse(BaseModel):
    ok: bool = True
    message: str = "success"


class DiffLine(BaseModel):
    type: str  # equal | added | removed | hunk
    old_no: int | None = None
    new_no: int | None = None
    text: str


class DiffResult(BaseModel):
    from_version: int
    to_version: int
    added: int
    removed: int
    changed_ratio: float
    lines: list[DiffLine] = Field(default_factory=list)
