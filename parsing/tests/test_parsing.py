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
    assert msg["model"] == "__TBC__"  # 不编造模型名


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
    rep = ParseRouter(strategy=STRATEGY_AUTO).parse_document(
        b"", filename="blank.txt", mime="text/plain")
    tr = rep["traces"][0]
    assert tr["level"] == LEVEL_MULTIMODAL
    assert tr["degraded"] == "vision_unavailable"
    assert "未配置" in rep["results"][0]["text"] or "结构化降级" in rep["results"][0]["text"]
    # 网关无视觉模型，绝不能写成已跑通视觉理解
    assert "视觉理解" not in rep["results"][0]["text"] or "非视觉" in rep["results"][0]["text"]


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
    assert "结构化降级" in body or "待 OCR" in body or "兜底不可用" in body


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
def test_unconfigured_external_levels_never_claim_available():
    st = ParseRouter().status()
    levels = {x["level"]: x for x in st["levels"]}
    assert levels[LEVEL_LOCAL]["available"] is True
    assert levels["ocr_api"]["available"] is False
    assert levels[LEVEL_MULTIMODAL]["available"] is False
    assert levels["ocr_api"]["contract"]["endpoint"] == "__TBC__"
    assert st["vision_models_probe"]["has_vision"] is False
    assert len(st["vision_models_probe"]["models"]) == 10


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
