"""策略基类与注册表。

三级策略都实现同一个接口，路由器只认 `available()` 与 `parse()`。
换供应商时下游 chunks / Milvus / citation 契约一律不动——这是可插拔的意义。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from parsing.contracts import LEVEL_ORDER, ParseResult


class ParseStrategy(ABC):
    name: str = "base"
    level: str = "local"

    def available(self) -> bool:
        """该策略当前能否真正工作。未配置端点必须返回 False，不能假装可用。"""
        return True

    def unavailable_reason(self) -> str:
        return ""

    @abstractmethod
    def parse(self, payload: bytes, *, mime: str, page_no: int,
              context: dict[str, Any] | None = None) -> ParseResult:
        ...

    def more_expensive_than(self, other: str) -> bool:
        return LEVEL_ORDER.get(self.level, 0) > LEVEL_ORDER.get(other, 0)


REGISTRY: dict[str, type[ParseStrategy]] = {}


def register(cls: type[ParseStrategy]) -> type[ParseStrategy]:
    REGISTRY[cls.name] = cls
    return cls
