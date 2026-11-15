"""解析路由器：质量门禁 + 确定性升级 + 90:10 预算闸门 + 全流程留痕。

为什么不是「随机按 90:10 分流」（回答用户的问题）：
固定比例随机抽样没有工程意义——它既不能保证疑难页被送到最贵的链路，
也会让本来能自研解决的页白白付钱。这里的 0.10 是**上限/熔断线**：
正常文档应为 0% 触发，只有质量门禁判定「无文本层 / 置信度不足 /
表格结构不自洽」时才逐级升级；多模态层最多处理 10% 的页，超了就
显式标 `escalation_budget_exceeded` 并回退上一级结果，绝不静默失败。

三级：P0 自研 local → P1 收费 OCR API → P2 多模态 LLM 传图。
每一页都产出 ParseTrace，前端「解析实验室」把这张表直接画出来，
面试时可以把「为什么这一页升级了」逐条讲清楚。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from parsing.config import (ParseConfig, TEXT_ONLY_MODELS, VISION_CAPABLE_MODELS,
                            VISION_PROBE_NOTE, current_strategy)
from parsing.contracts import (
    LEVEL_LOCAL, LEVEL_OCR_API, LEVEL_MULTIMODAL, LEVEL_DEGRADED,
    STRATEGY_AUTO, STRATEGY_LOCAL_ONLY, STRATEGY_OCR_API,
    STRATEGY_MULTIMODAL, STRATEGY_OFF,
    ParseResult, ParseTrace, StrategyUnavailable,
)
from parsing.strategies.local import LocalStrategy
from parsing.strategies.multimodal import MultimodalStrategy
from parsing.strategies.ocr_api import OcrApiStrategy

PAGE_CHARS = 3000  # 与 backend/app/services/parser.py 的 page_chars 对齐


def _is_image_page(page: "PageInput") -> bool:
    """给结构化降级用：只有真正的图片页才套用「图片结构事实」模板。"""
    return page.mime.startswith("image/") or bool(page.context.get("single_image"))


def _merge_fallback(best: ParseResult, fallback: ParseResult) -> ParseResult:
    """降级时保留本级已解析出的正文，只追加降级说明。

    反例：一张「表格结构不自洽」但正文完整的页，如果最贵链路不可用就整页
    替换成降级提示，会把已经拿到的正文丢掉，比不升级还糟。
    """
    if best.text.strip() and best.level in (LEVEL_LOCAL, LEVEL_OCR_API):
        fallback.text = best.text + "\n\n" + fallback.text
        fallback.blocks = best.blocks + fallback.blocks
        fallback.signals = best.signals
        fallback.upstream = {**best.upstream, "fallback": fallback.source}
    return fallback


@dataclass
class PageInput:
    page_no: int
    payload: bytes
    mime: str
    context: dict[str, Any] = field(default_factory=dict)


class VisionBudget:
    """最贵链路的页数预算。线程安全由每个解析任务独占实例保证。"""

    def __init__(self, total_pages: int, ratio: float, min_slots: int = 1):
        self.total_pages = max(1, total_pages)
        self.ratio = max(0.0, min(1.0, ratio))
        self.slots = max(min_slots, math.ceil(self.total_pages * self.ratio))
        self.used = 0

    def allow(self) -> bool:
        return self.used < self.slots

    def consume(self) -> None:
        self.used += 1

    def as_dict(self) -> dict:
        return {"total_pages": self.total_pages, "ratio": self.ratio,
                "slots": self.slots, "used": self.used,
                "remaining": max(0, self.slots - self.used)}


class ParseRouter:
    def __init__(self, cfg: ParseConfig | None = None, strategy: str | None = None):
        self.cfg = cfg or ParseConfig()
        self.strategy = strategy or current_strategy()
        self.local = LocalStrategy(self.cfg)
        self.ocr = OcrApiStrategy(self.cfg)
        self.vision = MultimodalStrategy(self.cfg)

    # ------------------------------------------------------------------
    # 状态：给前端展示「哪一级真的可用」，不把未配置说成已接入
    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "strategy": self.strategy,
            "strategies": [
                {"id": STRATEGY_AUTO, "label": "自动（质量门禁逐级升级）", "recommended": True},
                {"id": STRATEGY_LOCAL_ONLY, "label": "仅自研 P0", "recommended": False},
                {"id": STRATEGY_OCR_API, "label": "强制收费 OCR P1", "recommended": False},
                {"id": STRATEGY_MULTIMODAL, "label": "强制多模态 P2", "recommended": False},
                {"id": STRATEGY_OFF, "label": "关闭（只登记不解析）", "recommended": False},
            ],
            "levels": [
                {"level": LEVEL_LOCAL, "name": "P0 自研", "available": True,
                 "source": "纯标准库 + 可选 pypdf/Pillow", "cost": "0"},
                {"level": LEVEL_OCR_API, "name": "P1 收费 OCR / 版面解析",
                 "available": self.ocr.available(),
                 "reason": "" if self.ocr.available() else self.ocr.unavailable_reason(),
                 "contract": self.ocr.contract(), "cost": "__TBC__"},
                {"level": LEVEL_MULTIMODAL, "name": "P2 多模态 LLM 传图兜底",
                 "available": self.vision.available(),
                 "reason": "" if self.vision.available() else self.vision.unavailable_reason(),
                 "contract": self.vision.contract(), "cost": "按网关 token 计费，单价 __TBC__"},
            ],
            "budget": {"vision_escalation_ratio": self.cfg.vision_escalation_ratio,
                       "vision_min_slots": self.cfg.vision_min_slots,
                       "meaning": "最贵链路最多占 10% 的页，是上限/熔断线，不是随机分流；"
                                  "P2 已接通后它是真实的成本控制点"},
            "thresholds": self.cfg.as_dict(),
            "vision_models_probe": {
                "gateway": "Intern-AI Discovery /models",
                "models": list(VISION_CAPABLE_MODELS) + list(TEXT_ONLY_MODELS),
                "vision_capable": list(VISION_CAPABLE_MODELS),
                "text_only": list(TEXT_ONLY_MODELS),
                "has_vision": True,
                "verified_by": "双图对照传图实测（2026-11-14）：不同数字/背景色/标签均精确命中",
                "note": VISION_PROBE_NOTE,
                "reprobe": "python -m parsing.vision_probe",
            },
        }

    # ------------------------------------------------------------------
    # 分页：大文件绝不整块进内存解析，按页范围切片
    # ------------------------------------------------------------------
    def split_pages(self, payload: bytes, filename: str, mime: str) -> list[PageInput]:
        lower = filename.lower()
        if lower.endswith(".pdf") or mime == "application/pdf":
            try:
                import io
                from pypdf import PdfReader  # type: ignore
                reader = PdfReader(io.BytesIO(payload))
                pages = []
                for i, page in enumerate(reader.pages, 1):
                    text = page.extract_text() or ""
                    pages.append(PageInput(page_no=i, payload=text.encode("utf-8"),
                                           mime="text/plain",
                                           context={"simulated_text_layer": True,
                                                    "pdf_page": i,
                                                    "text_layer_chars": len(text)}))
                return pages or [PageInput(1, b"", "application/pdf", {"no_text_layer": True})]
            except Exception:
                # 没有 pypdf 或 PDF 损坏：整文件当一页，交给质量门禁判「无文本层」
                return [PageInput(1, payload, "application/pdf", {"no_text_layer": True})]

        if mime.startswith("image/") or lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            return [PageInput(1, payload, mime or "image/png", {"single_image": True})]

        text = payload.decode("utf-8", errors="replace")
        chunks = [text[i:i + PAGE_CHARS] for i in range(0, len(text), PAGE_CHARS)] or [""]
        return [PageInput(i + 1, c.encode("utf-8"), mime or "text/plain",
                          {"text_slice": True, "total_slices": len(chunks)})
                for i, c in enumerate(chunks)]

    # ------------------------------------------------------------------
    # 质量门禁
    # ------------------------------------------------------------------
    def needs_upgrade(self, result: ParseResult) -> tuple[bool, str]:
        s = result.signals
        if s.ocr_needed:
            return True, "no_text_layer_or_image"
        if s.confidence < self.cfg.confidence_min:
            return True, "low_confidence"
        if not s.table_complete:
            return True, "table_structure_unstable"
        if result.degraded:
            return True, "degraded_" + result.degraded
        return False, ""

    # ------------------------------------------------------------------
    # 单页
    # ------------------------------------------------------------------
    def parse_page(self, page: PageInput, *, budget: VisionBudget,
                   doc_id: str = "", filename: str = "") -> tuple[ParseResult, ParseTrace]:
        t0 = time.time()
        trace = ParseTrace(doc_id=doc_id, filename=filename, page_no=page.page_no,
                           strategy=self.strategy, level=LEVEL_LOCAL)

        if self.strategy == STRATEGY_OFF:
            r = ParseResult(level=LEVEL_DEGRADED, degraded="strategy_off",
                            source="router:off", text="[解析策略已关闭，仅登记文件]")
            trace.level, trace.reason, trace.degraded = LEVEL_DEGRADED, "strategy_off", "strategy_off"
            return r, trace

        # 强制档：便于演示「同一份文件在不同策略下的轨迹差异」
        if self.strategy == STRATEGY_MULTIMODAL:
            return self._forced_vision(page, budget, trace, doc_id, filename, t0)
        if self.strategy == STRATEGY_OCR_API:
            return self._forced_ocr(page, budget, trace, doc_id, filename, t0)
        if self.strategy == STRATEGY_LOCAL_ONLY:
            r = self.local.parse(page.payload, mime=page.mime, page_no=page.page_no,
                                 context=page.context)
            _fill_trace(trace, r, "local_only_forced")
            return r, trace

        # ---- auto：质量门禁逐级升级 ----
        r = self.local.parse(page.payload, mime=page.mime, page_no=page.page_no,
                             context=page.context)
        _fill_trace(trace, r, "local_ok")
        need, reason = self.needs_upgrade(r)
        if not need:
            r.latency_ms = int((time.time() - t0) * 1000)
            trace.latency_ms = r.latency_ms
            return r, trace

        best = r
        trace.reason = reason

        # P1 OCR
        if self.ocr.available():
            try:
                cand = self.ocr.parse(page.payload, mime=page.mime, page_no=page.page_no,
                                      context=page.context)
                best = cand
                trace.upgraded_from = LEVEL_LOCAL
                trace.level = LEVEL_OCR_API
                need2, reason2 = self.needs_upgrade(cand)
                trace.reason = reason2 or reason
                if not need2:
                    _fill_trace(trace, best, reason)
                    best.latency_ms = int((time.time() - t0) * 1000)
                    trace.latency_ms = best.latency_ms
                    return best, trace
            except StrategyUnavailable as e:
                trace.notes = trace.notes + (f"ocr_api 跳过: {e}",)
        else:
            trace.notes = trace.notes + ("ocr_api 未配置，跳过（契约 __TBC__）",)

        # P2 多模态：先过预算闸门
        if not budget.allow():
            trace.budget_blocked = True
            trace.reason = "escalation_budget_exceeded"
            best.degraded = best.degraded or "escalation_budget_exceeded"
            trace.notes = trace.notes + (
                f"预算闸门：{budget.used}/{budget.slots} 页已用，回退到 {best.level} 结果",)
            _fill_trace(trace, best, trace.reason)
            best.latency_ms = int((time.time() - t0) * 1000)
            trace.latency_ms = best.latency_ms
            return best, trace

        budget.consume()
        trace.upgraded_from = best.level
        trace.level = LEVEL_MULTIMODAL
        if self.vision.available():
            try:
                best = self.vision.parse(page.payload, mime=page.mime,
                                         page_no=page.page_no, context=page.context)
                trace.reason = "vision_applied"
            except StrategyUnavailable as e:
                best = _merge_fallback(best, self.vision.structured_fallback(
                    page.payload, page_no=page.page_no, is_image=_is_image_page(page)))
                trace.reason = "vision_call_failed_fallback"
                trace.notes = trace.notes + (f"视觉调用失败: {e}",)
        else:
            best = _merge_fallback(best, self.vision.structured_fallback(
                page.payload, page_no=page.page_no, is_image=_is_image_page(page)))
            trace.reason = "vision_unavailable_fallback"

        _fill_trace(trace, best, trace.reason)
        best.latency_ms = int((time.time() - t0) * 1000)
        trace.latency_ms = best.latency_ms
        return best, trace

    # ------------------------------------------------------------------
    def _forced_ocr(self, page, budget, trace, doc_id, filename, t0):
        if self.ocr.available():
            try:
                r = self.ocr.parse(page.payload, mime=page.mime, page_no=page.page_no,
                                   context=page.context)
                _fill_trace(trace, r, "forced_ocr_api")
            except StrategyUnavailable:
                r = self.local.parse(page.payload, mime=page.mime, page_no=page.page_no,
                                     context=page.context)
                _fill_trace(trace, r, "forced_ocr_unavailable_fallback")
        else:
            r = self.local.parse(page.payload, mime=page.mime, page_no=page.page_no,
                                 context=page.context)
            r.degraded = "ocr_api_unavailable"
            _fill_trace(trace, r, "forced_ocr_unavailable_fallback")
        r.latency_ms = int((time.time() - t0) * 1000)
        trace.latency_ms = r.latency_ms
        return r, trace

    def _forced_vision(self, page, budget, trace, doc_id, filename, t0):
        if budget.allow():
            budget.consume()
        else:
            trace.budget_blocked = True
        if self.vision.available() and not trace.budget_blocked:
            try:
                r = self.vision.parse(page.payload, mime=page.mime, page_no=page.page_no,
                                      context=page.context)
                _fill_trace(trace, r, "forced_vision_applied")
            except StrategyUnavailable:
                r = self.vision.structured_fallback(page.payload, page_no=page.page_no,
                                                is_image=_is_image_page(page))
                _fill_trace(trace, r, "vision_call_failed_fallback")
        else:
            r = self.vision.structured_fallback(page.payload, page_no=page.page_no,
                                                is_image=_is_image_page(page))
            r.degraded = ("escalation_budget_exceeded" if trace.budget_blocked
                          else "vision_unavailable")
            _fill_trace(trace, r, "forced_vision_fallback")
        r.latency_ms = int((time.time() - t0) * 1000)
        trace.latency_ms = r.latency_ms
        return r, trace

    # ------------------------------------------------------------------
    def parse_document(self, payload: bytes, *, filename: str, mime: str = "",
                       doc_id: str = "") -> dict:
        pages = self.split_pages(payload, filename, mime)
        budget = VisionBudget(len(pages), self.cfg.vision_escalation_ratio,
                              self.cfg.vision_min_slots)
        results, traces = [], []
        for p in pages:
            r, t = self.parse_page(p, budget=budget, doc_id=doc_id, filename=filename)
            results.append(r)
            traces.append(t)
        levels: dict[str, int] = {}
        for t in traces:
            levels[t.level] = levels.get(t.level, 0) + 1
        return {
            "doc_id": doc_id, "filename": filename, "strategy": self.strategy,
            "page_count": len(pages),
            "budget": budget.as_dict(),
            "summary": {
                "by_level": levels,
                "upgrades": sum(1 for t in traces if t.upgraded_from),
                "budget_blocked": sum(1 for t in traces if t.budget_blocked),
                "degraded": sum(1 for t in traces if t.degraded),
                "total_latency_ms": sum(t.latency_ms for t in traces),
            },
            "traces": [t.as_dict() for t in traces],
            "results": [r.as_dict() for r in results],
            "note": ("P1 收费 OCR 未接通，契约字段保持 __TBC__；"
                     "P2 已接通 Discovery 网关的视觉能力（deepseek-v4.1-flash-1017，"
                     "双图对照实测通过）。auto 策略仅在质量门禁命中且预算闸门允许时才调用 P2；"
                     "设置 PARSING_OFFLINE=1 可全程零外部请求"),
        }


def _fill_trace(trace: ParseTrace, result: ParseResult, reason: str) -> None:
    trace.level = result.level
    trace.confidence = result.signals.confidence
    trace.latency_ms = result.latency_ms
    trace.reason = reason or trace.reason
    trace.degraded = result.degraded
    trace.notes = trace.notes + tuple(result.signals.notes)
