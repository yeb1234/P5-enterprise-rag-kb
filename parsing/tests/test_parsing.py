"""解析子系统离线单测：零外部调用、零网络、零真实模型流量。

覆盖：契约 / 图片容器头 / 表格结构语义 / 质量门禁 / 三级策略 / 预算闸门 /
降级保留正文 / 未配置端点绝不发请求 / 分页与大文件。
"""
from __future__ import annotations

import pytest

from parsing import images, tables
from parsing.config import ParseConfig
from parsing.contracts import (LEVEL_LOCAL, LEVEL_MULTIMODAL, STRATEGY_AUTO,
                               STRATEGY_LOCAL_ONLY, STRATEGY_OFF, STRATEGY_MULTIMODAL)
from parsing.router import ParseRouter, VisionBudget, _is_image_page, PageInput
from parsing.strategies.multimodal import MultimodalStrategy
from parsing.strategies.ocr_api import OcrApiStrategy


# ---------------------------------------------------------------- 表格
def test_table_structure_semantics():
    lines = ["| 型号 | 状态 | 数量 |", "|---|---|---|",
             "| A1 | 就绪 | 12.5% |", "| A2 | 异常 | 3 |"]
    t = tables.parse_table_lines(lines)
    assert t is not None and t["kind"] == "markdown"
    assert (t["n_rows"], t["n_cols"]) == (3, 3)
    assert all(c["passed"] for c in t["checks"])
    # 结构语义：单元格带行列坐标、类型与单位
    cell = next(c for c in t["cells"] if c["value"] == "12.5%")
    assert cell["type"] == "percent" and cell["unit"] == "%"
    assert cell["row"] == 1 and cell["col"] == 2
    assert "[行2·列1]" in tables.table_to_context(t)


def test_table_ragged_row_reduces_confidence():
    t = tables.parse_table_lines(["| a | b | c |", "|---|---|---|", "| 1 | 2 |"])
    # 列数守恒检查应失败，置信度相应下调
    assert not all(c["passed"] for c in t["checks"])
    assert t["confidence"] < 1.0


def test_extract_tables_splits_plain_text():
    text = "前言一段\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n结尾一段"
    ts, plain = tables.extract_tables(text)
    assert len(ts) == 1
    assert "前言一段" in plain and "结尾一段" in plain


# ---------------------------------------------------------------- 图片
def test_png_header_probe():
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
           + bytes([0, 0, 0, 10, 0, 0, 0, 20, 8, 6, 0, 0, 0]) + b"\x00" * 4)
    f = images.probe_image(png)
    assert f["format"] == "png" and f["width"] == 10 and f["height"] == 20


def test_vision_message_has_data_uri():
    msg = images.vision_message("描述图片", b"\x89PNG", "image/png")
    content = msg["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # 2026-11-14 实测：模型名是网关 /models 里的真实 id，不再占位
    assert msg["model"] == "deepseek-v4.1-flash-1017"
    assert msg["thinking"] == {"type": "disabled"}
    assert msg["temperature"] == 0


def test_unknown_bytes_are_not_reported_as_image():
    f = images.probe_image(b"")
    assert f["format"] == "unknown" and f["probe"] == "header_only"


# ---------------------------------------------------------------- 质量门禁
def test_plain_text_stays_local():
    rep = ParseRouter(strategy=STRATEGY_AUTO).parse_document(
        "第一章 设备手册\n\n长按复位键进入配网模式，指示灯变蓝后即可绑定账号。".encode(),
        filename="manual.md", mime="text/markdown")
    assert rep["summary"]["by_level"] == {LEVEL_LOCAL: 1}
    assert rep["traces"][0]["reason"] == "local_ok"
    assert rep["traces"][0]["degraded"] is None


def test_no_text_layer_escalates_and_degrades_honestly():
    """离线开关打开时，P2 跳过并留痕，不能把降级结果说成视觉理解。"""
    rep = ParseRouter(strategy=STRATEGY_AUTO).parse_document(
        b"", filename="blank.txt", mime="text/plain")
    tr = rep["traces"][0]
    assert tr["level"] == LEVEL_MULTIMODAL
    assert tr["degraded"] == "vision_unavailable"
    assert "结构化降级" in rep["results"][0]["text"]
    assert "PARSING_OFFLINE" in rep["results"][0]["text"]
    assert "视觉理解已跑通" not in rep["results"][0]["text"]


def test_pdf_without_text_layer_is_flagged():
    from parsing.strategies.local import LocalStrategy
    r = LocalStrategy().parse(b"%PDF-1.7\n" + bytes(range(256)), mime="application/pdf", page_no=1)
    assert r.signals.ocr_needed is True
    assert r.degraded == "no_text_layer"


def test_degradation_keeps_local_text():
    """表格结构坏但正文完整时，降级不能把正文整页丢掉。"""
    text = ("| a | b | c |\n|---|---|---|\n| 1 | 2 |\n" * 20).encode()
    rep = ParseRouter(strategy=STRATEGY_AUTO).parse_document(
        text, filename="ragged.md", mime="text/markdown")
    body = rep["results"][0]["text"]
    assert "| a | b | c |" in body  # 本级正文仍在
    assert "结构化降级" in body or "待 OCR" in body or "兜底已跳过" in body


# ---------------------------------------------------------------- 预算闸门
def test_budget_is_ceiling_not_random_split():
    b = VisionBudget(20, 0.10, 1)
    assert b.slots == 2
    assert b.allow() and b.allow()
    b.consume(); b.consume()
    assert not b.allow()


def test_budget_blocked_marks_trace():
    """多页全部触发升级时，超出 10% 的页必须显式标 budget_blocked。"""
    router = ParseRouter(strategy=STRATEGY_AUTO)
    # 构造 20 个空文本页，每页都会走质量门禁升级
    pages = [PageInput(i + 1, b"", "text/plain", {"text_slice": True}) for i in range(20)]
    budget = VisionBudget(20, 0.10, 1)
    traces = [router.parse_page(p, budget=budget)[1] for p in pages]
    assert sum(1 for t in traces if t.budget_blocked) > 0
    assert budget.used == budget.slots
    assert any(t.reason == "escalation_budget_exceeded" for t in traces if t.budget_blocked)


# ---------------------------------------------------------------- 端点可用性
def test_offline_switch_and_p1_contract_are_honest():
    """离线开关下 P2 不可用，但网关能力的事实不能被改成「无视觉模型」。"""
    st = ParseRouter().status()
    levels = {x["level"]: x for x in st["levels"]}
    assert levels[LEVEL_LOCAL]["available"] is True
    assert levels["ocr_api"]["available"] is False          # P1 未接通
    assert levels[LEVEL_MULTIMODAL]["available"] is False   # 离线开关
    assert "PARSING_OFFLINE" in levels[LEVEL_MULTIMODAL]["reason"]
    assert levels["ocr_api"]["contract"]["endpoint"] == "__TBC__"
    probe = st["vision_models_probe"]
    assert probe["has_vision"] is True
    assert len(probe["models"]) == 10
    assert len(probe["vision_capable"]) == 6
    assert "deepseek-v4.1-flash-1017" in probe["vision_capable"]


def test_config_snapshot_of_gateway_modalities():
    from parsing.config import TEXT_ONLY_MODELS, VISION_CAPABLE_MODELS
    assert len(VISION_CAPABLE_MODELS) == 6 and len(TEXT_ONLY_MODELS) == 4
    assert "deepseek-v4.1-flash-1017" in VISION_CAPABLE_MODELS
    assert not (set(VISION_CAPABLE_MODELS) & set(TEXT_ONLY_MODELS))


def test_vision_layer_available_when_online(monkeypatch):
    """关掉离线开关后，P2 复用现成网关即可用，无需额外凭证。"""
    from dataclasses import replace
    monkeypatch.setenv("PARSING_OFFLINE", "0")
    monkeypatch.setenv("PARSING_VISION_ENABLED", "1")
    cfg = replace(ParseConfig(), vision_base_url="https://example.invalid/v1",
                  vision_api_key="test-key", vision_model="deepseek-v4.1-flash-1017")
    assert MultimodalStrategy(cfg).available() is True
    assert ParseRouter(cfg=cfg).status()["levels"][2]["available"] is True


def test_vision_parse_maps_real_response(monkeypatch):
    """mock urlopen 验证响应映射，不消耗真实流量。"""
    import json
    from dataclasses import replace
    monkeypatch.setenv("PARSING_OFFLINE", "0")
    cfg = replace(ParseConfig(), vision_base_url="https://example.invalid/v1",
                  vision_api_key="test-key")
    s = MultimodalStrategy(cfg)
    assert s.available() is True
    payload = {
        "choices": [{"message": {"content": "73492 on a light blue background."}}],
        "usage": {"prompt_tokens": 267, "completion_tokens": 90, "total_tokens": 357,
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }

    class _Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp())
    r = s.parse(b"\x89PNG", mime="image/png", page_no=1)
    assert r.level == LEVEL_MULTIMODAL and r.degraded is None
    assert "73492" in r.text
    assert r.upstream["usage"]["total_tokens"] == 357
    assert r.upstream["reasoning_tokens"] == 0
    assert s.build_request(b"\x89PNG")["thinking"] == {"type": "disabled"}


def test_vision_empty_content_triggers_fallback(monkeypatch):
    import json
    from dataclasses import replace
    from parsing.contracts import StrategyUnavailable
    monkeypatch.setenv("PARSING_OFFLINE", "0")
    cfg = replace(ParseConfig(), vision_base_url="https://example.invalid/v1",
                  vision_api_key="test-key")
    s = MultimodalStrategy(cfg)

    class _Resp:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": ""}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp())
    with pytest.raises(StrategyUnavailable):
        s.parse(b"\x89PNG", mime="image/png", page_no=1)


def test_ocr_strategy_raises_when_unconfigured():
    from parsing.contracts import StrategyUnavailable
    with pytest.raises(StrategyUnavailable):
        OcrApiStrategy(ParseConfig()).parse(b"x", mime="application/pdf", page_no=1)


def test_vision_fallback_for_non_image_page_is_not_image_template():
    fb = MultimodalStrategy(ParseConfig()).structured_fallback(
        b"", page_no=1, is_image=False)
    assert "图片结构事实" not in fb.text
    assert fb.degraded == "vision_unavailable"
    assert _is_image_page(PageInput(1, b"", "text/plain")) is False


# ---------------------------------------------------------------- 策略开关与分页
def test_forced_strategies_produce_distinct_traces():
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30
    levels = {}
    for s in (STRATEGY_AUTO, STRATEGY_LOCAL_ONLY, STRATEGY_MULTIMODAL, STRATEGY_OFF):
        rep = ParseRouter(strategy=s).parse_document(data, filename="p.png", mime="image/png")
        levels[s] = rep["traces"][0]["level"]
    assert levels[STRATEGY_LOCAL_ONLY] == LEVEL_LOCAL
    assert levels[STRATEGY_OFF] == "degraded"
    assert levels[STRATEGY_MULTIMODAL] == LEVEL_MULTIMODAL


def test_large_text_split_into_pages():
    router = ParseRouter()
    pages = router.split_pages(b"x" * 3000 * 12, "big.txt", "text/plain")
    assert len(pages) == 12
    assert all(len(p.payload) <= 3000 for p in pages)


def test_parse_document_summary_counts():
    rep = ParseRouter().parse_document(
        b"# t\n\n" + b"a" * 5000, filename="big.md", mime="text/markdown")
    assert rep["page_count"] == 2
    assert sum(rep["summary"]["by_level"].values()) == 2
    assert len(rep["traces"]) == 2 and len(rep["results"]) == 2
