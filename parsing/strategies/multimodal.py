"""P2 多模态 LLM 传图兜底（请求构造已就绪，端点待补）。

已实查的事实（2026-11-14）：Intern-AI Discovery 网关 `/models` 返回 10 个模型
（Agents-A1 / Atria-Dawn / Atria-Dawn-Preview / Sage-A1.5 / Sage-A1.5-Taiyi /
deepseek-v4.1-flash-1017 / glm-5.3 / intern-s2 / kimi-k3.1-pro / qwen3.8-27b），
**全部纯文本，没有视觉模型**；硅基流动侧只有 bge-m3 嵌入与 bge-reranker-v2-m3 重排。
因此本层的当前状态是：
- `vision_message()` 能构造标准 OpenAI 兼容的 `image_url` + base64 data URI 请求；
- `available()` 在未配置 `PARSING_VISION_*` 时返回 False；
- 未配置时走「结构化降级」：把 `parsing.images` 的可测事实序列化成受限文本，
  由纯文本 LLM 做**非视觉**的受限推断（默认不自动调用，避免消耗流量），
  并明确标 `vision_unavailable`，绝不宣称完成了图片理解。
"""
from __future__ import annotations

import time
from typing import Any

from parsing import images
from parsing.config import ParseConfig
from parsing.contracts import (Block, LEVEL_MULTIMODAL, ParseResult, QualitySignals,
                               StrategyUnavailable)
from parsing.strategies.base import ParseStrategy, register

VISION_PROMPT = (
    "请只依据图片中的可见文字与表格作答，不要推测未显示的信息；"
    "输出结构：1) 文档类型 2) 可见标题 3) 表格结构（行列）4) 关键数值 5) 不确定的部分。"
)


@register
class MultimodalStrategy(ParseStrategy):
    name = "multimodal"
    level = LEVEL_MULTIMODAL

    def __init__(self, cfg: ParseConfig | None = None):
        self.cfg = cfg or ParseConfig()

    def available(self) -> bool:
        return bool(self.cfg.vision_base_url and self.cfg.vision_api_key and self.cfg.vision_model)

    def unavailable_reason(self) -> str:
        return ("PARSING_VISION_BASE_URL / API_KEY / MODEL 未配置；"
                "已实查 Discovery 网关 10 个模型均无视觉能力")

    def contract(self) -> dict[str, str]:
        return {
            "endpoint": "__TBC__",
            "auth": "__TBC__",
            "request_schema": "OpenAI 兼容 messages[].content: text + image_url(data URI)",
            "response_schema": "__TBC__",
            "quotas": "__TBC__",
            "error_codes": "__TBC__",
            "model_version": "__TBC__",
            "pricing": "__TBC__",
        }

    def build_request(self, payload: bytes, *, mime: str = "image/png") -> dict:
        """只构造、不发送。供前端「查看请求长什么样」与后续接入复用。"""
        return images.vision_message(VISION_PROMPT, payload, mime)

    def parse(self, payload: bytes, *, mime: str, page_no: int,
              context: dict[str, Any] | None = None) -> ParseResult:
        t0 = time.time()
        if not self.available():
            raise StrategyUnavailable(self.unavailable_reason())

        # 有端点时构造请求并发送；本轮没有端点，这段是接入后要补的唯一点。
        # 用标准库 urllib，避免新增依赖；响应结构保持 __TBC__，接入时按契约映射。
        import json
        import os
        import urllib.request
        body = self.build_request(payload, mime=mime or "image/png")
        body["model"] = self.cfg.vision_model
        req = urllib.request.Request(
            self.cfg.vision_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.cfg.vision_api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except Exception as e:
            raise StrategyUnavailable(f"视觉端点调用失败: {type(e).__name__}") from e
        return ParseResult(
            text=text,
            blocks=[Block(block_type="paragraph", text=text, page_no=page_no, confidence=0.8)],
            signals=QualitySignals(text_chars=len(text), confidence=0.8,
                                   notes=("多模态端点返回", "图片文字仍应以 OCR 结果为准")),
            level=LEVEL_MULTIMODAL, source="vision:" + self.cfg.vision_model,
            latency_ms=int((time.time() - t0) * 1000),
            upstream={"provider": "__TBC__", "page_no": page_no},
        )

    def structured_fallback(self, payload: bytes, *, page_no: int,
                            is_image: bool = True) -> ParseResult:
        """无视觉端点时的**结构化降级**：只输出可测事实，不编造内容。

        非图片页（例如「无文本层的空白 PDF 页」）不能套用图片事实模板，
        否则会把一页空白文本描述成「图片结构事实」，属于失真。
        """
        if not is_image:
            desc = ("[多模态兜底不可用 · 结构化降级]\n"
                    "本页既没有可提取文本，也不是可由容器头解析的图片；"
                    "当前网关无视觉模型、OCR 端点未配置（契约 __TBC__），"
                    "无法给出内容。已留痕，不编造文本。")
            return ParseResult(
                text=desc,
                blocks=[Block(block_type="paragraph", text=desc, page_no=page_no,
                              confidence=0.2)],
                signals=QualitySignals(confidence=0.2, ocr_needed=True,
                                       notes=("vision_unavailable", "no_text_layer",
                                              "非图片页，未套用图片事实模板")),
                level=LEVEL_MULTIMODAL, degraded="vision_unavailable",
                source="vision:structured-fallback-no-text",
            )
        facts = images.probe_image(payload)
        desc = images.image_to_text_facts(facts)
        return ParseResult(
            text=desc,
            blocks=[Block(block_type="image", text=desc, page_no=page_no,
                          facts=facts, confidence=0.35)],
            signals=QualitySignals(confidence=0.35,
                                   ocr_needed=True, image_ratio=1.0,
                                   notes=("vision_unavailable，走结构化降级",
                                          "网关实查无视觉模型，未发送任何图片请求")),
            level=LEVEL_MULTIMODAL,
            degraded="vision_unavailable",
            source="vision:structured-fallback",
        )
