"""自适应重排单测（不触网）：判据命中就跳过外部 rerank，未命中照旧。

守三件事：
1. top1 领先 ≥ 阈值 → 不调用 rerank，meta 里留痕 `skipped:margin=...`；
2. 分得很近 → 照旧调用，结果仍按重排分数排；
3. 阈值置 0（关闭）→ 即使大幅领先也必须重排，避免「悄悄关掉重排」这种事故。
另有两条边界：只有 1 条候选时跳过（重排无从改顺序）；词法回退路径没有向量分时不判定。
"""
import pytest

from webapp import providers, rag, settings


def _cands(scores: list[float]):
    """按传进来的向量分造候选（chunk_id 用假值，_attach_texts 查不到也不影响）。"""
    return [
        {"chunk_id": f"c{i}", "doc_id": f"d{i}", "idx": 0,
         "text": f"设备配网步骤 {i}：长按复位键", "title": f"配网{i}"}
        for i in range(len(scores))
    ]


def _install(monkeypatch, scores: list[float], margin: float | None = None):
    """把检索链路替换成「门禁命中 + 固定向量分」，并把 rerank 换成计数器。"""
    cands = _cands(scores)

    def fake_semantic(q, c, cfg):
        for x, s in zip(c, scores):
            x["_emb_score"] = s
            x["score"] = round(s, 4)
        c.sort(key=lambda x: x["_emb_score"], reverse=True)
        return c

    calls = []

    def fake_rerank(base_url, model, query, docs, api_key=""):
        calls.append(len(docs))
        # 故意把顺序倒过来，便于验证「重排生效」
        return [float(i) for i in range(len(docs))]

    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "embedding_provider": "local",
                                 "rerank_provider": "external"})
    monkeypatch.setattr(rag, "_gate_hit", lambda terms: True)
    monkeypatch.setattr(rag, "_load_light_chunks", lambda: [dict(c) for c in cands])
    monkeypatch.setattr(rag, "_semantic_rank", fake_semantic)
    monkeypatch.setattr(providers, "rerank", fake_rerank)
    if margin is not None:
        monkeypatch.setattr(rag, "ADAPTIVE_RERANK_MARGIN", margin)
    return calls


def test_skip_when_top1_leads(monkeypatch):
    calls = _install(monkeypatch, [0.81, 0.70, 0.69, 0.68, 0.67, 0.66])
    hits, meta = rag.retrieve("如何配网", with_meta=True)
    assert calls == [], "top1 领先 0.11，不该再调 rerank"
    assert meta["rerank"].startswith("skipped:margin=")
    assert [h["chunk_id"] for h in hits[:2]] == ["c0", "c1"], "跳过后应保持向量顺序"


def test_rerank_when_close(monkeypatch):
    calls = _install(monkeypatch, [0.664, 0.660, 0.655, 0.650, 0.645, 0.640])
    hits, meta = rag.retrieve("如何配网", with_meta=True)
    assert calls == [6], "间距 0.004 < 阈值，必须照旧重排"
    assert meta["rerank"] == "applied:external"
    assert [h["chunk_id"] for h in hits[:2]] == ["c5", "c4"], "重排分数倒序应生效"


def test_margin_zero_disables_gate(monkeypatch):
    calls = _install(monkeypatch, [0.95, 0.60, 0.59, 0.58, 0.57, 0.56], margin=0.0)
    _, meta = rag.retrieve("如何配网", with_meta=True)
    assert calls == [6], "阈值 0 = 关闭，必须重排"
    assert meta["rerank"] == "applied:external"


def test_single_candidate_skipped(monkeypatch):
    calls = _install(monkeypatch, [0.40])
    _, meta = rag.retrieve("如何配网", with_meta=True)
    assert calls == [], "只有 1 条候选时重排无从改顺序，直接跳过"
    assert meta["rerank"] == "skipped:single"


def test_no_emb_score_does_not_skip(monkeypatch):
    """词法回退路径（没有向量分）不判定：宁可贵一点，也不要在没依据时省。"""
    _install(monkeypatch, [0.9, 0.6, 0.5])

    def lexical_rank(q, c, cfg):
        for x in c:
            x["_lex_score"] = 1
        return c

    monkeypatch.setattr(rag, "_semantic_rank", lexical_rank)
    calls = []
    monkeypatch.setattr(providers, "rerank",
                        lambda *a, **k: (calls.append(1), [1.0, 0.0, 0.0])[1])
    _, meta = rag.retrieve("如何配网", with_meta=True)
    assert calls, "没有 _emb_score 时必须照常重排"
    assert not meta["rerank"].startswith("skipped")
