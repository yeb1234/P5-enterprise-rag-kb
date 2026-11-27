"""文档解析子系统的数据契约。

设计取舍（为什么先定契约再写策略）：
- `backend/app/services/parser.py` 已经把 ChunkSpec 的页级元数据
  （page_no / section_path / block_type / confidence / source_level）固定下来；
  本包只负责「把原始字节变成结构化文本 + 质量信号」，不碰下游 chunks 表、
  Milvus schema 与 citation 契约。换任何一家 OCR / 视觉服务都只换策略实现。
- 所有「是否升级」的判断都必须落到可序列化的 QualitySignals 上，
  不靠运行时猜测。这样解析轨迹能在前端逐页展示，评审时可讲清每一次升级的依据。
- 全部用标准库 dataclass，零第三方依赖即可导入；可选依赖（pypdf / Pillow）
  只在具体策略里按需引入，缺失时优雅降级。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 策略级别：数字越大越贵、越慢，也越可能引入外部依赖
LEVEL_LOCAL = "local"              # P0 自研：纯标准库 / 可选 pypdf + Pillow
LEVEL_OCR_API = "ocr_api"          # P1 收费 OCR / 版面解析 API（适配器）
LEVEL_MULTIMODAL = "multimodal"    # P2 多模态 LLM 传图兜底
LEVEL_DEGRADED = "degraded"        # 全部不可用时的结构化占位

LEVEL_ORDER = {LEVEL_LOCAL: 0, LEVEL_OCR_API: 1, LEVEL_MULTIMODAL: 2, LEVEL_DEGRADED: 3}

# 前端可选策略：auto 走质量门禁自动升级，其余为强制档（便于演示对照）
STRATEGY_AUTO = "auto"
STRATEGY_LOCAL_ONLY = "local_only"
STRATEGY_OCR_API = "ocr_api"
STRATEGY_MULTIMODAL = "multimodal"
STRATEGY_OFF = "off"
STRATEGIES = (STRATEGY_AUTO, STRATEGY_LOCAL_ONLY, STRATEGY_OCR_API,
              STRATEGY_MULTIMODAL, STRATEGY_OFF)


@dataclass(frozen=True)
class QualitySignals:
    """一页的质量信号。所有阈值判断都基于这些可复算的字段。"""

    text_chars: int = 0                  # 可提取的字符数
    text_density: float = 0.0           # 字符数 / 估计页面面积（字符/千像素² 的粗略归一）
    image_ratio: float = 0.0            # 图片区域占比估计 0..1
    table_cells: int = 0                # 识别到的表格单元格数
    table_complete: bool = True         # 表格结构是否自洽（列数守恒等）
    ocr_needed: bool = False            # 是否判定为「无文本层 / 需要 OCR」
    confidence: float = 1.0             # 本级结果置信度 0..1
    notes: tuple[str, ...] = ()         # 人类可读的判定依据，前端逐条展示

    def as_dict(self) -> dict[str, Any]:
        return {
            "text_chars": self.text_chars,
            "text_density": round(self.text_density, 4),
            "image_ratio": round(self.image_ratio, 4),
            "table_cells": self.table_cells,
            "table_complete": self.table_complete,
            "ocr_needed": self.ocr_needed,
            "confidence": round(self.confidence, 4),
            "notes": list(self.notes),
        }


@dataclass
class Block:
    """结构化块。表格块带 cells，图片块带 facts，普通段落只有 text。"""

    block_type: str                      # paragraph | table | image | heading
    text: str = ""
    page_no: int | None = None
    section_path: str = ""
    cells: list[dict] | None = None
    facts: dict[str, Any] | None = None
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "block_type": self.block_type, "text": self.text,
            "page_no": self.page_no, "section_path": self.section_path,
            "cells": self.cells, "facts": self.facts,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class ParseResult:
    """单页解析结果。level 记录最终生效的策略级别。"""

    text: str = ""
    blocks: list[Block] = field(default_factory=list)
    signals: QualitySignals = field(default_factory=QualitySignals)
    level: str = LEVEL_LOCAL
    degraded: str | None = None          # 具体降级原因码，None 表示正常
    latency_ms: int = 0
    source: str = ""                     # 实现标识，便于轨迹里区分
    upstream: dict[str, Any] = field(default_factory=dict)  # 预留：外部服务返回的原始元数据（脱敏）

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "blocks": [b.as_dict() for b in self.blocks],
            "signals": self.signals.as_dict(),
            "level": self.level, "degraded": self.degraded,
            "latency_ms": self.latency_ms, "source": self.source,
            # 外部调用的模型、token 用量与推理 token 数必须回传：
            # P2 已接通并真实计费，轨迹里要能看到每次调用花了什么。
            "upstream": dict(self.upstream),
        }


@dataclass
class ParseTrace:
    """一页的完整解析轨迹。演示时前端直接把这张表画出来。"""

    doc_id: str = ""
    filename: str = ""
    page_no: int = 1
    strategy: str = STRATEGY_AUTO
    level: str = LEVEL_LOCAL
    confidence: float = 1.0
    latency_ms: int = 0
    upgraded_from: str | None = None     # 从哪一级升上来的
    budget_blocked: bool = False         # 是否被 90:10 预算闸门拦下
    reason: str = ""                     # 升级 / 降级 / 拦截的原因码
    degraded: str | None = None
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id, "filename": self.filename, "page_no": self.page_no,
            "strategy": self.strategy, "level": self.level,
            "confidence": round(self.confidence, 4), "latency_ms": self.latency_ms,
            "upgraded_from": self.upgraded_from, "budget_blocked": self.budget_blocked,
            "reason": self.reason, "degraded": self.degraded,
            "notes": list(self.notes),
        }


class StrategyUnavailable(RuntimeError):
    """策略需要的端点/依赖未配置。路由必须捕获它并降级，绝不让上传链路 500。"""
