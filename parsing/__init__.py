"""可插拔文档解析子系统。

目录职责（自解释）：
  contracts.py  数据契约（QualitySignals / ParseResult / ParseTrace / 策略常量）
  config.py     运行时开关与阈值（PARSING_ 前缀，与其它命名空间隔离）
  images.py     图片容器头解析 + 传图请求构造
  tables.py     表格结构语义还原与稳定性校验
  router.py     质量门禁 + 确定性升级 + 90:10 预算闸门 + 逐页轨迹
  strategies/   三级策略：local / ocr_api / multimodal
  tests/        离线单测，不触发任何外部调用

快速使用：
    from parsing.router import ParseRouter
    report = ParseRouter().parse_document(data, filename="manual.pdf", mime="application/pdf")
    print(report["summary"], report["traces"])
"""
from parsing.contracts import (
    LEVEL_LOCAL, LEVEL_OCR_API, LEVEL_MULTIMODAL, LEVEL_DEGRADED,
    STRATEGIES, ParseResult, ParseTrace, QualitySignals,
)
from parsing.config import ParseConfig, current_strategy, set_strategy
from parsing.router import ParseRouter, VisionBudget

__all__ = [
    "ParseRouter", "VisionBudget", "ParseConfig",
    "ParseResult", "ParseTrace", "QualitySignals",
    "LEVEL_LOCAL", "LEVEL_OCR_API", "LEVEL_MULTIMODAL", "LEVEL_DEGRADED",
    "STRATEGIES", "current_strategy", "set_strategy",
]
