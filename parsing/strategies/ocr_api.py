"""P1 收费 OCR / 版面解析 API 适配器（只留契约，不编造接口）。

纪律：本项目**尚未接入任何收费 OCR 服务**。外部接口地址、鉴权、请求/响应结构、
配额、错误码、模型版本、价格一律 `__TBC__`，不推断填写（与 `eval/` 冻结口径一致）。
本文件的价值是把「换供应商」这件事收敛到一个类里：
- `available()` 依据是否配置了 `PARSING_OCR_BASE_URL` + `PARSING_OCR_API_KEY`；
- 未配置时返回 False，路由器捕获后降级并留痕，绝不假装跑过 OCR；
- 真接入时只需实现 `_call()`，下游 chunks / Milvus / citation 契约不动。
"""
from __future__ import annotations

import time
from typing import Any

from parsing.config import ParseConfig
from parsing.contracts import (LEVEL_OCR_API, ParseResult, StrategyUnavailable)
from parsing.strategies.base import ParseStrategy, register


@register
class OcrApiStrategy(ParseStrategy):
    name = "ocr_api"
    level = LEVEL_OCR_API

    def __init__(self, cfg: ParseConfig | None = None):
        self.cfg = cfg or ParseConfig()

    def available(self) -> bool:
        return bool(self.cfg.ocr_api_base_url and self.cfg.ocr_api_key)

    def unavailable_reason(self) -> str:
        return "PARSING_OCR_BASE_URL / PARSING_OCR_API_KEY 未配置（接口契约 __TBC__）"

    def contract(self) -> dict[str, str]:
        """给前端与文档展示的契约槽位，全部 __TBC__。"""
        return {
            "endpoint": "__TBC__",
            "auth": "__TBC__",
            "request_schema": "__TBC__",
            "response_schema": "__TBC__",
            "quotas": "__TBC__",
            "error_codes": "__TBC__",
            "model_version": "__TBC__",
            "pricing": "__TBC__",
        }

    def _call(self, payload: bytes, *, mime: str) -> dict[str, Any]:
        """真实调用点。接入时实现：POST 到端点、带鉴权、解析版面 JSON。

        当前**故意不实现**：没有凭证就没有可验证的请求/响应结构，
        写一个假的成功返回会污染质量结论。
        """
        raise StrategyUnavailable(self.unavailable_reason())

    def parse(self, payload: bytes, *, mime: str, page_no: int,
              context: dict[str, Any] | None = None) -> ParseResult:
        t0 = time.time()
        if not self.available():
            raise StrategyUnavailable(self.unavailable_reason())
        data = self._call(payload, mime=mime)
        # 接入后把响应映射成统一结构；字段结构以契约为准，此处只做最小骨架。
        text = str(data.get("text", ""))
        return ParseResult(
            text=text,
            blocks=[],
            level=LEVEL_OCR_API,
            source="ocr_api:__TBC__",
            latency_ms=int((time.time() - t0) * 1000),
            upstream={"provider": "__TBC__", "page_no": page_no},
        )
