"""showcase 数据契约离线单测（零外部调用，喂真实解析产物）。

覆盖 `01-scope.md` V5 与 `03-phase2-spec.md` §1.1(4)(8)：
同参双跑投影逐字节一致、改参必变、投影不误删解析原因码与源文件摘要、
compact/full 字段差异、四出口规则单点且最终文本只脱敏一次。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from showcase.guard import protect_text  # noqa: E402
from showcase.ingest_preview import (NON_REPLAY_FIELDS, build_views,  # noqa: E402
                                     canonical, canonical_dumps, chunk_text)
from showcase.params import DEFAULT_AS_OF, Params  # noqa: E402

SAMPLES = ROOT / "showcase" / "samples"


def _rep(stem: str) -> dict:
    return json.loads((SAMPLES / f"{stem}.report.json").read_text(encoding="utf-8"))


def _route(route_id: str = "rt-a", hits: int = 1, tier: str = "standard") -> dict:
    return {
        "route_id": route_id, "tier": tier, "model": "", "model_snapshot_at": None,
        "snapshot_version": "snap-stable", "prompt": "", "prompt_version": "",
        "params": {}, "fallback_chain": ["standard", "fallback"],
        "trigger": "difficulty_threshold", "cost_hint": {"estimated": False},
        "placeholder": True, "reason": f"连续命中 {hits}", "difficulty": 0.005,
        "signals": {}, "hysteresis": {"consecutive_hits": hits, "state": tier},
        "decided_at": DEFAULT_AS_OF,
    }


def _pii() -> dict:
    _, info = protect_text("手机 13812345678", True)
    return info


def _make(stem: str = "simple", **overrides):
    params = Params(**overrides)
    payload = (SAMPLES / ("simple.pdf" if stem == "simple" else "complex.png")).read_bytes()
    rep = _rep(stem)
    route = _route()
    _, ing = build_views(
        payload, filename="simple.pdf" if stem == "simple" else "complex.png",
        mime="application/pdf" if stem == "simple" else "image/png",
        params=params, rep=rep, route=route, pii=_pii(), run_id="RUN-FIXED-0001")
    return params, payload, rep, route, ing


# ---------- V5：确定性投影 ----------

def test_canonical_double_run_is_byte_identical():
    """同参双跑：决策实例字段（route_id / hysteresis / reason / manifest）不同，投影仍一致。"""
    # 两边必须是同一档位（这里用 full），只让「决策实例字段」不同；
    # 若一边 compact 一边 full，字段集合本身就不同，比较没有意义。
    a = _make(ingest_format="full")[4]
    b = _make(ingest_format="full")[4]
    b["llm_route"] = dict(b["llm_route"])
    b["llm_route"]["route_id"] = "rt-b"
    b["llm_route"]["reason"] = "连续命中 2/2"
    b["llm_route"]["hysteresis"] = {"consecutive_hits": 2, "state": "standard"}
    b["manifest"] = {"path": "other.json", "sha256": "other", "snapshot_version": "x"}
    assert canonical_dumps(a) == canonical_dumps(b)


def test_canonical_keeps_trace_reason_and_source_sha():
    """投影必须保留解析原因码与源文件摘要，否则会掩盖真实差异（假阳性）。"""
    ing = _make()[4]
    out = canonical(ing)
    assert all("reason" in t for t in out.get("trace", []) if isinstance(t, dict))
    assert out["source"]["sha256"] == ing["source"]["sha256"]


def test_canonical_detects_real_changes():
    ing = _make()[4]
    changed = json.loads(json.dumps(ing, ensure_ascii=False))
    changed["source"]["sha256"] = "changed"
    assert canonical_dumps(changed) != canonical_dumps(ing)
    changed2 = json.loads(json.dumps(ing, ensure_ascii=False))
    changed2["chunks"][0]["text"] += "X"
    assert canonical_dumps(changed2) != canonical_dumps(ing)


def test_change_chunk_size_changes_projection():
    base = _make(chunk_size_chars=800)[4]
    other = _make(chunk_size_chars=400)[4]
    assert canonical_dumps(base) != canonical_dumps(other)
    assert len(other["chunks"]) >= len(base["chunks"])


def test_non_replay_fields_declared():
    assert {"latency_ms", "created_at", "run_id", "generated_at"} <= set(NON_REPLAY_FIELDS)


# ---------- 入库文档形态 ----------

def test_ingest_created_at_is_fixed_as_of_and_no_wall_clock():
    ing = _make()[4]
    assert ing["created_at"] == DEFAULT_AS_OF
    assert "wall_clock" not in json.dumps(ing, ensure_ascii=False)
    assert "elapsed_ms" not in json.dumps(ing, ensure_ascii=False)


def test_compact_and_full_field_sets_differ():
    full = _make(ingest_format="full")[4]
    compact = _make(ingest_format="compact")[4]
    assert "guardrails" in full and "guardrails" not in compact
    assert "cost" in full and "cost" not in compact
    assert set(compact["llm_route"]) == {
        "route_id", "tier", "model", "snapshot_version", "placeholder"}
    assert len(compact["llm_route"]) < len(full["llm_route"])
    assert "confidence" not in compact["chunks"][0]
    assert "confidence" in full["chunks"][0]
    assert "note" in compact  # 回放标注必须保留（spec §1.1(2)）


def test_llm_route_is_embedded_not_referenced():
    ing = _make()[4]
    assert ing["llm_route"]["route_id"] == _route()["route_id"]
    assert isinstance(ing["llm_route"], dict)


def test_chunk_ids_are_stable_and_content_derived():
    a = _make()[4]["chunks"][0]["chunk_id"]
    b = _make()[4]["chunks"][0]["chunk_id"]
    assert a == b and len(a) == 12


# ---------- 分块与四出口 ----------

def test_chunk_text_edges():
    assert chunk_text("", 800, 80) == []
    assert chunk_text("   ", 800, 80) == []
    with pytest.raises(ValueError):
        chunk_text("abc", 100, 100)
    with pytest.raises(ValueError):
        chunk_text("abc", 0, 0)
    parts = chunk_text("A" * 1600, 800, 80)
    assert len(parts) == 3 and parts[0][-80:] == parts[1][:80]


def test_protect_text_four_gates_agree_and_single_redaction():
    text = "员工身份证号是 110101199003071938，手机 13812345678。"
    safe, info = protect_text(text, True)
    hits = [r["hit_count"] for r in info["chain"]]
    assert len(hits) == 4 and len(set(hits)) == 1 and hits[0] > 0
    assert "110101199003071938" not in safe
    assert info["report"]["blocked"] is False
    assert info["rule_version"] == "PII-RULES@1.0"
    # 最终文本只取第一次 ingest，不重复打码
    assert safe == info["chain"][0]["text"]


def test_protect_text_disabled_is_passthrough_with_full_report():
    text = "手机 13812345678"
    safe, info = protect_text(text, False)
    assert safe == text
    assert info["report"]["total_hits"] == 0
    assert info["report"]["blocked"] is False
    assert info["chain"] == []
