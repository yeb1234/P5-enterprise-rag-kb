"""三级解析策略。注册靠各模块底部的 @register 装饰器。

导入本包即完成注册，路由器按 name 取类：
  local        P0 自研（零外部服务）
  ocr_api      P1 收费 OCR / 版面解析（适配器，契约 __TBC__）
  multimodal   P2 多模态 LLM 传图兜底（端点待补，含结构化降级）
"""
from parsing.strategies.base import ParseStrategy, REGISTRY, register
from parsing.strategies.local import LocalStrategy
from parsing.strategies.ocr_api import OcrApiStrategy
from parsing.strategies.multimodal import MultimodalStrategy

# 确保三个策略类都已注册（模块导入时装饰器已执行，这里做一次显式断言）
for _cls in (LocalStrategy, OcrApiStrategy, MultimodalStrategy):
    REGISTRY.setdefault(_cls.name, _cls)

__all__ = ["ParseStrategy", "REGISTRY", "register",
           "LocalStrategy", "OcrApiStrategy", "MultimodalStrategy"]
