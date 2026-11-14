"""解析策略的运行时配置。

命名空间隔离：一律用 `PARSING_` 前缀。项目里已有 `WEBAPP_`（演示切片的
provider 开关）与 backend 的扁平配置，共用名字会让两边启动校验互相污染。
阈值都是**演示默认经验起始值**，不是压测结论，需要真实语料校准。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from parsing.contracts import STRATEGIES, STRATEGY_AUTO


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ParseConfig:
    # --- 质量门禁阈值 ---
    # 可提取字符少于该值 → 判定为「无文本层 / 需 OCR」
    ocr_text_chars_min: int = _i("PARSING_OCR_TEXT_CHARS_MIN", 40)
    # 低于该置信度 → 允许升级到下一级
    confidence_min: float = _f("PARSING_CONFIDENCE_MIN", 0.75)
    # 表格检查失败比例超过该值 → 判定表格结构不可信，建议升级到版面解析
    table_check_fail_ratio: float = _f("PARSING_TABLE_FAIL_RATIO", 0.4)
    # 图片字节熵超过该值且文本极少 → 视为「以位图为主」
    image_entropy_min: float = _f("PARSING_IMAGE_ENTROPY_MIN", 7.2)

    # --- 预算闸门（用户问的 90:10）---
    # 含义是「最贵的多模态兜底最多占多少比例的页」，是**上限/熔断线**，
    # 不是随机分流：正常文档应为 0% 触发，只有质量门禁判疑难才升级。
    vision_escalation_ratio: float = _f("PARSING_VISION_ESCALATION_RATIO", 0.10)
    # 单次任务至少允许 1 页升级，避免小文件被比例算成 0
    vision_min_slots: int = _i("PARSING_VISION_MIN_SLOTS", 1)

    # --- 外部端点（未提供时适配器自动不可用，全部保持 __TBC__）---
    ocr_api_base_url: str = os.environ.get("PARSING_OCR_BASE_URL", "")
    ocr_api_key: str = os.environ.get("PARSING_OCR_API_KEY", "")
    ocr_api_model: str = os.environ.get("PARSING_OCR_MODEL", "")
    vision_base_url: str = os.environ.get("PARSING_VISION_BASE_URL", "")
    vision_api_key: str = os.environ.get("PARSING_VISION_API_KEY", "")
    vision_model: str = os.environ.get("PARSING_VISION_MODEL", "")
    # 别名为现有 Discovery 网关；实查 10 个模型全为纯文本，故默认 False
    discovery_as_vision: bool = os.environ.get("PARSING_DISCOVERY_AS_VISION", "false").lower() == "true"

    def as_dict(self) -> dict:
        return {
            "ocr_text_chars_min": self.ocr_text_chars_min,
            "confidence_min": self.confidence_min,
            "table_check_fail_ratio": self.table_check_fail_ratio,
            "image_entropy_min": self.image_entropy_min,
            "vision_escalation_ratio": self.vision_escalation_ratio,
            "vision_min_slots": self.vision_min_slots,
            "ocr_configured": bool(self.ocr_api_base_url and self.ocr_api_key),
            "vision_configured": bool(self.vision_base_url and self.vision_api_key and self.vision_model),
            "discovery_as_vision": self.discovery_as_vision,
            "threshold_note": "演示默认经验起始值，非压测结论，需用真实语料校准",
        }


def current_strategy() -> str:
    s = os.environ.get("PARSING_STRATEGY", STRATEGY_AUTO)
    return s if s in STRATEGIES else STRATEGY_AUTO


def set_strategy(value: str) -> str:
    """设置进程内策略（演示切片）；生产应落配置中心并带版本号。"""
    if value not in STRATEGIES:
        raise ValueError(f"未知解析策略: {value}")
    os.environ["PARSING_STRATEGY"] = value
    return value
