"""P2 多模态 LLM 传图兜底（已接通，2026-11-14 实测跑通）。

实测事实（此前「网关无视觉模型」的判断是错的，只看了模型名）：
- 网关 `/models` 的 `input_modalities` 显示 10 个模型里 6 个支持 `image`：
  Agents-A1 / Sage-A1.5 / deepseek-v4.1-flash-1017 / intern-s2 /
  kimi-k3.1-pro / qwen3.8-27b。目标模型支持 `url` 与 `base64` 两种图片来源。
- 用 OpenAI 兼容 `image_url` + base64 data URI 做双图对照：图 A（浅蓝/73492/CODE-A）
  与图 B（浅黄/18620/CODE-B）都被精确识别且回答不同，耗时 1406 ms / 2463 ms。
- `thinking={"type":"disabled"}` 实测把 reasoning_tokens 从 55 降到 0、
  耗时 979 ms → 467 ms；元数据里的布尔 `reasoning` 参数实测无效。

因此本层默认复用现成的 `DISCOVERY_*` 网关，无需额外凭证；
`PARSING_VISION_*` 可覆盖成别的端点，`PARSING_OFFLINE=1` 一键全离线。
`structured_fallback()` 不再是默认路径，只用于离线开关或调用失败时兜底。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

from parsing import images
from parsing.config import ParseConfig, _flag
from parsing.contracts import (Block, LEVEL_MULTIMODAL, ParseResult, QualitySignals,
                               StrategyUnavailable)
from parsing.strategies.base import ParseStrategy, register

VISION_PROMPT = (
    "请只依据图片中可见的内容作答，不要推测未显示的信息。"
    "输出结构：1) 文档/图片类型 2) 可见标题与文字 3) 表格结构（行列）"
    "4) 关键数值与颜色 5) 不确定的部分。"
    "注意：精确数字与文字应以 OCR 结果为准，请标注哪些是肉眼可见、哪些是推断。"
)


@register
class MultimodalStrategy(ParseStrategy):
    name = "multimodal"
    level = LEVEL_MULTIMODAL

    def __init__(self, cfg: ParseConfig | None = None):
        self.cfg = cfg or ParseConfig()

    def available(self) -> bool:
        # 动态读 env，保证单测 setenv 立即生效（ParseConfig 的字段默认值在 import 时求值）。
        if _flag("PARSING_OFFLINE", False):
            return False
        if not _flag("PARSING_VISION_ENABLED", True):
            return False
        return bool(self.cfg.vision_base_url and self.cfg.vision_api_key and self.cfg.vision_model)

    def unavailable_reason(self) -> str:
        if _flag("PARSING_OFFLINE", False):
            return "PARSING_OFFLINE=1，已按总离线开关跳过视觉调用"
        if not _flag("PARSING_VISION_ENABLED", True):
            return "PARSING_VISION_ENABLED=0，视觉层已手动关闭"
        return "视觉端点未配置（PARSING_VISION_* 与 DISCOVERY_* 均为空）"

    def contract(self) -> dict[str, str]:
        return {
            "endpoint": "复用 DISCOVERY_BASE_URL 的 /chat/completions（值在 .env，不落代码）",
            "auth": "Bearer DISCOVERY_API_KEY（值在 .env，不下发前端）",
            "request_schema": "OpenAI 兼容 messages[].content: text + image_url(data URI)（已实测）",
            "response_schema": "choices[0].message.content + usage（已实测）",
            "image_sources": "url / base64（/models 元数据 input_modalities）",
            "low_thinking": "thinking={'type':'disabled'}（实测 reasoning_tokens 55 → 0）",
            "quotas": "__TBC__",
            "error_codes": "__TBC__",
            "model_version": self.cfg.vision_model + "（/models 实查 active）",
            "pricing": "__TBC__",
        }

    def build_request(self, payload: bytes, *, mime: str = "image/png") -> dict:
        """构造可直接发送的请求体；前端「查看请求长什么样」也复用它。"""
        return images.vision_message(
            VISION_PROMPT, payload, mime or "image/png",
            model=self.cfg.vision_model,
            low_thinking=self.cfg.vision_low_thinking,
            max_tokens=1024,
        )

    def parse(self, payload: bytes, *, mime: str, page_no: int,
              context: dict[str, Any] | None = None) -> ParseResult:
        t0 = time.time()
        if not self.available():
            raise StrategyUnavailable(self.unavailable_reason())

        body = self.build_request(payload, mime=mime or "image/png")
        req = urllib.request.Request(
            self.cfg.vision_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.cfg.vision_api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.vision_timeout_s) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            raise StrategyUnavailable(f"视觉端点调用失败: {type(e).__name__}") from e

        choices = data.get("choices") or []
        text = ((choices[0].get("message", {}) if choices else {}).get("content") or "").strip()
        if not text:
            raise StrategyUnavailable("视觉端点返回空正文，转结构化降级")
        usage = data.get("usage") or {}
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        return ParseResult(
            text=text,
            blocks=[Block(block_type="paragraph", text=text, page_no=page_no, confidence=0.8)],
            signals=QualitySignals(
                text_chars=len(text), confidence=0.8,
                notes=("多模态端点真实返回", "图片精确文字/数字仍应以 OCR 结果为准",
                       f"reasoning_tokens={reasoning}"),
            ),
            level=LEVEL_MULTIMODAL, source="vision:" + self.cfg.vision_model,
            latency_ms=int((time.time() - t0) * 1000),
            upstream={"provider": "Discovery 兼容端点", "model": self.cfg.vision_model,
                      "page_no": page_no,
                      "usage": {k: usage.get(k) for k in
                                ("prompt_tokens", "completion_tokens", "total_tokens")},
                      "reasoning_tokens": reasoning},
        )

    def structured_fallback(self, payload: bytes, *, page_no: int,
                            is_image: bool = True, reason: str = "") -> ParseResult:
        """离线开关 / 调用失败时的**结构化降级**：只输出可测事实，不编造内容。

        非图片页不能套用「图片结构事实」模板，否则会把空白文本页描述成图片。
        """
        why = reason or self.unavailable_reason()
        if not is_image:
            desc = ("[多模态兜底已跳过 · 结构化降级]\n"
                    "本页既没有可提取文本，也不是可由容器头解析的图片；"
                    f"原因：{why}。P1 收费 OCR 未接通（契约 __TBC__），"
                    "无法给出内容。已留痕，不编造文本。")
            return ParseResult(
                text=desc,
                blocks=[Block(block_type="paragraph", text=desc, page_no=page_no,
                              confidence=0.2)],
                signals=QualitySignals(confidence=0.2, ocr_needed=True,
                                       notes=("vision_skipped", "no_text_layer", why,
                                              "非图片页，未套用图片事实模板")),
                level=LEVEL_MULTIMODAL, degraded="vision_unavailable",
                source="vision:structured-fallback-no-text",
            )
        facts = images.probe_image(payload)
        desc = images.image_to_text_facts(facts) + f"\n[多模态兜底已跳过] 原因：{why}。"
        return ParseResult(
            text=desc,
            blocks=[Block(block_type="image", text=desc, page_no=page_no,
                          facts=facts, confidence=0.35)],
            signals=QualitySignals(confidence=0.35,
                                   ocr_needed=True, image_ratio=1.0,
                                   notes=("vision_skipped，走结构化降级", why,
                                          "未发送任何图片请求，不编造内容")),
            level=LEVEL_MULTIMODAL,
            degraded="vision_unavailable",
            source="vision:structured-fallback",
        )
