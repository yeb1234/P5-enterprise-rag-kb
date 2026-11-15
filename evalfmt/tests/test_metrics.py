"""指标函数单测：算错一个分母，整份报告就不可信，所以这里必须钉死。"""
from __future__ import annotations

from evalfmt import metrics


def test_recall_and_mrr_boundaries():
    assert metrics.recall_at_k(1, 1) == 1.0
    assert metrics.recall_at_k(3, 3) == 1.0
    assert metrics.recall_at_k(4, 3) == 0.0
    assert metrics.recall_at_k(None, 5) == 0.0
    assert metrics.reciprocal_rank(1) == 1.0
    assert metrics.reciprocal_rank(4) == 0.25
    assert metrics.reciprocal_rank(None) == 0.0


def test_aggregate_retrieval_uses_correct_denominator():
    scores = [
        metrics.RetrievalScore("Q1", "a", 1, "a", True),
        metrics.RetrievalScore("Q2", "b", 3, "a", False),
        metrics.RetrievalScore("Q3", "c", None, "a", False),
    ]
    agg = metrics.aggregate_retrieval(scores)
    assert agg["n"] == 3
    assert abs(agg["recall@1"] - 1 / 3) < 1e-9
    assert abs(agg["recall@3"] - 2 / 3) < 1e-9
    assert abs(agg["recall@5"] - 2 / 3) < 1e-9
    assert abs(agg["mrr"] - (1 + 1 / 3 + 0) / 3) < 1e-9
    assert abs(agg["citation_precision@1"] - 1 / 3) < 1e-9


def test_contains_normalizes_but_does_not_fuzzy_match():
    assert metrics.contains("额定功率 850W", "850W")
    assert metrics.contains("IP54 防护", " ip54 ")
    assert metrics.contains("保修 24 个月", "24个月")
    # 不能做同义/数量级改写，否则幻觉会被算成命中
    assert not metrics.contains("额定功率 0.85kW", "850W")


def test_hallucination_flags_unsupported_tokens_only():
    evidence = "型号 KX-2400，防护等级 IP54，端口 18443"
    answer = "KX-2400 的防护等级是 IP54，端口 18443，保修 36 个月。"
    rate, bad, total = metrics.hallucination(answer, evidence)
    assert total >= 3
    assert metrics.norm("36 个月") in {metrics.norm(x) for x in bad}
    assert 0 < rate < 1
    # KX-2400 / IP54 / 18443 都在证据里，不该被判为编造
    assert not any(metrics.norm(x) in ("kx-2400", "ip54", "18443") for x in bad)


def test_hallucination_none_when_no_fact_claims():
    rate, bad, total = metrics.hallucination("这个问题建议咨询现场工程师。", "任意证据")
    assert rate is None and bad == [] and total == 0


def test_refusal_and_trap():
    assert metrics.refused("知识库中未找到 MX-9900 的保修信息。")
    assert not metrics.refused("MX-9900 保修 24 个月。")
    # 把 KX 的 24 个月套到 MX 上，且没有任何澄清 → 违规
    assert metrics.trap_violated("MX-9900 的保修期是 24 个月。", "24 个月")
    # 引述了 24 个月但同时说明无资料 → 不算违规
    assert not metrics.trap_violated(
        "KX-2400 保修 24 个月，但知识库中未找到 MX-9900 的资料。", "24 个月")


def test_hallucination_ignores_entities_echoed_from_question():
    """复述问题里的实体不算幻觉——幻觉是「说出无依据的事实」，不是「提到库外名字」。"""
    evidence = "KX-2400 的整机保修 24 个月"
    answer = "知识库中未找到 MX-9900 的资料，现有参考只涉及 KX-2400。"
    rate, bad, total = metrics.hallucination(answer, evidence, "MX-9900 的保修期是多久？")
    assert bad == []
    assert rate == 0.0 and total >= 1


def test_hallucination_extracts_ip_addresses():
    rate, bad, total = metrics.hallucination("默认网关是 10.66.8.1", "网关 10.66.8.1")
    assert total == 1 and bad == [] and rate == 0.0
    rate2, bad2, _ = metrics.hallucination("默认网关是 10.0.0.9", "网关 10.66.8.1")
    assert bad2 == ["10.0.0.9"] and rate2 == 1.0


def test_norm_handles_glyph_variants_not_fuzzy():
    """同义字形要对齐（× vs x），但不许放宽到模糊匹配。"""
    assert metrics.contains("尺寸 320×210×95 mm", "320x210x95")
    assert metrics.contains("功率 850W", "850W")
    assert not metrics.contains("功率 0.85kW", "850W")
