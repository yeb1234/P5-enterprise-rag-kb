"""知识治理离线单测：零外部调用、零模型流量。

覆盖时间衰减 / 适用性门控 / 过期处置 / 重排对照 / claim 抽取防误报 /
冲突分类 / 连通分量 / 按簇裁决 / 证据记录契约。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from knowledge import (Claim, DocMeta, EvidenceRecord, FreshnessPolicy,
                        age_days, decay_factor, detect_and_build, detect_conflicts,
                        evaluate, extract_claims, meta_from_row, rerank, summary,
                        topic_similarity)
from knowledge.contracts import (ACTION_DEMOTE, ACTION_EXCLUDE, ACTION_WARN,
                                 KIND_AUTHORITY, KIND_FRESHNESS,
                                 RESOLVE_AUTHORITY, RESOLVE_ESCALATE,
                                 RESOLVE_RECENCY)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _iso(days: int = 0) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future(days: int = 3) -> str:
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 新鲜度
def test_decay_is_half_life_and_floored():
    p = FreshnessPolicy(half_life_days=90, demote_floor=0.1)
    assert decay_factor(0, 90, 0.1) == 1.0
    assert abs(decay_factor(90, 90, 0.1) - 0.5) < 1e-9
    assert abs(decay_factor(180, 90, 0.1) - 0.25) < 1e-9
    assert decay_factor(9999, 90, 0.1) == 0.1          # 不会归零


def test_age_uses_last_verified_before_indexed():
    m = DocMeta(doc_id="d", indexed_at=_iso(300), last_verified_at=_iso(10))
    assert age_days(m, NOW) == pytest.approx(10, abs=0.01)


def test_not_yet_effective_is_excluded_not_judged_stale():
    """未来生效的政策不能进上下文，但也不能被标成「过期」。"""
    m = DocMeta(doc_id="d", valid_from=_future(3), indexed_at=_iso(1))
    r = evaluate(m, 0.9, policy=FreshnessPolicy(), now=NOW)
    assert r.not_yet_effective and r.excluded and r.effective_score == 0
    assert not r.is_expired and not r.is_stale


def test_expired_actions_are_distinct():
    m = DocMeta(doc_id="d", expires_at=_iso(1), indexed_at=_iso(10))
    a = evaluate(m, 0.9, policy=FreshnessPolicy(expired_action=ACTION_EXCLUDE), now=NOW)
    b = evaluate(m, 0.9, policy=FreshnessPolicy(expired_action=ACTION_DEMOTE), now=NOW)
    c = evaluate(m, 0.9, policy=FreshnessPolicy(expired_action=ACTION_WARN), now=NOW)
    assert a.excluded and a.effective_score == 0
    assert b.action == ACTION_DEMOTE and 0 < b.effective_score < c.effective_score
    assert c.action == ACTION_WARN and c.effective_score == pytest.approx(c.base_score * (1 - 0.65 + 0.65 * c.decay))


def test_stale_does_not_override_relevance_completely():
    m = DocMeta(doc_id="d", indexed_at=_iso(800))
    p = FreshnessPolicy(half_life_days=90, stale_after_days=365)
    r = evaluate(m, 1.0, policy=p, now=NOW)
    assert r.is_stale and r.decay == 0.1
    assert r.effective_score > 0.3   # 高度相关老文档仍保留


def test_rerank_keeps_before_after_evidence():
    """开关前后可对照：original_rank / base_score 必须保留。"""
    metas = {
        "old": DocMeta("old", indexed_at=_iso(700)),
        "new": DocMeta("new", indexed_at=_iso(1)),
        "future": DocMeta("future", valid_from=_future(2)),
    }
    hits = [{"doc_id": "old", "score": 0.95, "chunk_id": "c1"},
            {"doc_id": "new", "score": 0.80, "chunk_id": "c2"},
            {"doc_id": "future", "score": 0.99, "chunk_id": "c3"}]
    kept, results = rerank(hits, metas, policy=FreshnessPolicy(), now=NOW)
    assert [h["doc_id"] for h in kept] == ["new", "old"]        # future 被排除，new 反超 old
    assert all("freshness" in h for h in kept)
    assert results[0].original_rank == 1 and results[0].base_score == 0.95
    # 关掉新鲜度开关只关「时间衰减」，适用性门控仍然生效：
    # 未来生效的政策必须排除，否则会把尚未生效的内容喂给模型。
    off, off_results = rerank(hits, metas, policy=FreshnessPolicy(enabled=False), now=NOW)
    assert off[0]["doc_id"] == "old"                 # 恢复按相关分排序
    assert [h["doc_id"] for h in off] == ["old", "new"]
    assert off_results[2].not_yet_effective and off_results[2].excluded


def test_summary_reports_age_distribution_and_alert():
    metas = [DocMeta(str(i), indexed_at=_iso(d)) for i, d in enumerate((5, 60, 200, 800))]
    rs = [evaluate(m, 1.0, policy=FreshnessPolicy(), now=NOW) for m in metas]
    s = summary(rs, policy=FreshnessPolicy(stale_alert_ratio=0.3))
    assert s["count"] == 4 and s["stale"] == 1
    assert s["buckets"]["0-30d"] == 1 and s["buckets"][">365d"] == 1
    assert s["alert"] is False  # 0.25 < 0.3


def test_meta_from_row_tolerates_missing_and_json_scope():
    m = meta_from_row({"id": "d1", "filename": "a.md", "scope": '{"region":"CN"}',
                       "authority": "3", "created_at": _iso(2)})
    assert m.doc_id == "d1" and m.title == "a.md" and m.authority == 3
    assert m.scope == {"region": "CN"} and m.indexed_at


# ---------------------------------------------------------------- claim 抽取
def test_extract_numeric_claim_with_unit():
    cs = extract_claims("婚假天数为 10 天。", doc_id="d1")
    assert len(cs) == 1
    assert cs[0].value_norm == "10" and cs[0].unit == "天"
    assert cs[0].attribute == "duration_days"
    assert cs[0].text  # 支持片段必须保留


def test_year_and_small_integer_are_not_conflict_fuel():
    """年份（1900–2099 无单位）与裸小整数 1–9 是公开实践里的主要误报源。"""
    year = extract_claims("发布日期为 2026 年。", doc_id="d1")
    # 「年」不在单位表内，归一化后置信度被压低
    assert not year or year[0].confidence < 0.5
    small = extract_claims("阶段数为 3。", doc_id="d1")
    assert not small or small[0].confidence < 1.0


def test_negation_sets_polarity():
    cs = extract_claims("远程办公固定工位不再需要。", doc_id="d1")
    assert cs and cs[0].polarity == -1 and cs[0].value_norm == "false"


def test_state_asymmetry_detects_conflict():
    """一份说「需要」、一份说「不再需要」：否定不对称必须被检出。"""
    a = extract_claims("固定工位需要。", doc_id="policy-v1")
    b = extract_claims("固定工位不再需要。", doc_id="policy-v2")
    assert a and b
    assert a[0].polarity == 1 and b[0].polarity == -1
    cfs = detect_conflicts(a + b)
    assert len(cfs) == 1 and cfs[0].kind in ("negation", "unresolved")


def test_table_row_is_a_claim():
    cs = extract_claims("| 接口超时 | 30 秒 |", doc_id="d1")
    assert len(cs) == 1 and cs[0].value_norm == "30" and cs[0].unit == "秒"


def test_topic_similarity_gates_unrelated_topics():
    assert topic_similarity("退款期限是多少天", "退款期限天数") >= 0.3
    assert topic_similarity("退款期限是多少天", "接口超时时间配置") < 0.3


# ---------------------------------------------------------------- 冲突检测
def _claim(subject: str, value: str, *, doc_id: str = "d", polarity: int = 1,
           authority: int = 1, valid_from: str | None = None,
           scope: dict | None = None, confidence: float = 1.0) -> Claim:
    return Claim(claim_id=doc_id + "-" + value, doc_id=doc_id, subject=subject,
                 attribute="value", value=value, value_norm=value, polarity=polarity,
                 authority=authority, valid_from=valid_from, scope=scope or {},
                 text=f"{subject}为{value}", confidence=confidence)


def test_same_value_is_corroboration_not_conflict():
    cs = [_claim("婚假天数", "10", doc_id="a"), _claim("婚假天数", "10", doc_id="b")]
    assert detect_conflicts(cs) == []


def test_numeric_conflict_detected():
    cs = [_claim("婚假天数", "10", doc_id="a"), _claim("婚假天数", "15", doc_id="b")]
    cfs = detect_conflicts(cs)
    assert len(cfs) == 1 and set(cfs[0].values) == {"10", "15"}


def test_different_scope_is_not_conflict():
    """不同地区/套餐的同一字段取值不同，是适用性差异，不是事实冲突。"""
    a = _claim("婚假天数", "10", doc_id="a", scope={"region": "CN"})
    b = _claim("婚假天数", "15", doc_id="b", scope={"region": "GB"})
    assert a.key != b.key
    assert detect_conflicts([a, b]) == []


def test_low_confidence_does_not_trigger_conflict():
    a = _claim("阶段数", "3", doc_id="a", confidence=0.4)
    b = _claim("阶段数", "4", doc_id="b", confidence=0.4)
    assert detect_conflicts([a, b]) == []


# ---------------------------------------------------------------- 裁决与证据
def test_authority_wins_without_review():
    a = _claim("婚假天数", "10", doc_id="a", authority=1)
    b = _claim("婚假天数", "15", doc_id="b", authority=3)
    rec = detect_and_build([a, b])
    cf = rec.conflicts[0]
    assert cf.kind == KIND_AUTHORITY and cf.resolution == RESOLVE_AUTHORITY
    assert cf.winner_id == b.claim_id and cf.needs_review is False


def test_recency_wins_but_requires_review():
    """新版本优先只是先验，绝不自动采信。"""
    a = _claim("婚假天数", "10", doc_id="a", valid_from=_iso(300))
    b = _claim("婚假天数", "15", doc_id="b", valid_from=_iso(1))
    rec = detect_and_build([a, b])
    cf = rec.conflicts[0]
    assert cf.kind == KIND_FRESHNESS and cf.resolution == RESOLVE_RECENCY
    assert cf.needs_review is True and cf.winner_id == b.claim_id


def test_equal_authority_escalates_without_inventing_tiebreak():
    a = _claim("婚假天数", "10", doc_id="a")
    b = _claim("婚假天数", "15", doc_id="b")
    rec = detect_and_build([a, b])
    cf = rec.conflicts[0]
    assert cf.resolution == RESOLVE_ESCALATE and cf.winner_id is None
    assert cf.severity == "block" and cf.needs_review is True
    assert any("不得自行挑选" in w for w in rec.warnings)


def test_evidence_record_keeps_both_sides_and_logs_decision():
    a = _claim("婚假天数", "10", doc_id="a")
    b = _claim("婚假天数", "15", doc_id="b", authority=3)
    c = _claim("接口超时", "30", doc_id="c")
    rec = detect_and_build([a, b, c], as_of="2026-11-14T00:00:00Z")
    assert rec.conflict_signal is True
    assert len(rec.claims) == 3                       # 所有 claim 都保留
    assert c.claim_id in rec.selected_claim_ids      # 未冲突的照常进入
    assert a.claim_id in rec.excluded_claim_ids      # 被淘汰的留痕而非删除
    assert rec.resolution_log and rec.resolution_log[0]["reason"]
    d = rec.as_dict()
    assert d["conflict_signal"] is True and "note" in d


def test_end_to_end_from_text_to_evidence():
    """两段真实文本 → 抽取 → 冲突 → 证据，全链路可跑。"""
    t1 = "婚假天数为 10 天。\n接口超时为 30 秒。"
    t2 = "婚假天数为 15 天。\n接口超时为 30 秒。"
    cs = extract_claims(t1, doc_id="policy-v1") + extract_claims(t2, doc_id="policy-v2")
    rec = detect_and_build(cs)
    assert rec.conflict_signal is True
    assert len(rec.conflicts) == 1                   # 超时一致，不应误报
    assert set(rec.conflicts[0].values) == {"10", "15"} or "天" in "".join(
        rec.conflicts[0].values)
