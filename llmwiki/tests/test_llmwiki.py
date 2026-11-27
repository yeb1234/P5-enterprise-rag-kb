"""llmwiki 单测：门禁、构建（证据链校验）、检索。只用假模型，不触网。

这组测试钉住的是 LLM Wiki 最容易做错的两件事：
- **门禁不能是摆设**：场景未定 / 信源变了 / 有过期内容，任一条不满足都必须不启动；
- **证据链不能「模型给了就存」**：指向素材之外的证据、原话对不上的证据，一律丢弃并计数。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from llmwiki import builder, gate, retriever

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


def _docs(*hashes, **extra) -> list[dict]:
    return [{"doc_id": f"d{i}", "revision": "1", "updated_at": "2026-11-01T00:00:00Z",
             "created_at": "2026-11-01T00:00:00Z", "content_hash": h, **extra}
            for i, h in enumerate(hashes)]


CFG = {"wiki_scenario": "智能设备排障", "wiki_stale_ratio_max": "0.1"}


def _built(docs, scenario="智能设备排障"):
    return {"scenario": scenario, "source_fingerprint": gate.source_fingerprint(docs)}


# ---------------------------------------------------------------- 门禁
def test_gate_passes_when_all_three_stable():
    g = gate.evaluate_gate(CFG, _docs("a", "b"), None, now=NOW)  # 先看没构建时
    assert not g["allowed"] and "尚未构建 Wiki" in g["reasons"]
    g = gate.evaluate_gate(CFG, _docs("a", "b"), _built(_docs("a", "b")), now=NOW)
    assert g["allowed"] and g["reasons"] == []
    assert {k: v["ok"] for k, v in g["checks"].items()} == {
        "scenario": True, "sources": True, "freshness": True}


def test_gate_blocks_when_scenario_missing():
    g = gate.evaluate_gate({"wiki_scenario": "  ", "wiki_stale_ratio_max": "0.1"}, _docs("a"), None, now=NOW)
    assert not g["checks"]["scenario"]["ok"] and not g["allowed"]


def test_gate_blocks_when_scenario_changed_after_build():
    docs = _docs("a")
    g = gate.evaluate_gate(CFG, docs, _built(docs, scenario="别的场景"), now=NOW)
    assert not g["checks"]["scenario"]["ok"]
    assert any("场景在构建后变更" in r for r in g["reasons"])


def test_gate_blocks_when_source_changed():
    """原文被改过一个字，指纹就变——页面是基于旧内容整理的，不能再当事实用。"""
    g = gate.evaluate_gate(CFG, _docs("a2"), _built(_docs("a1")), now=NOW)
    assert not g["checks"]["sources"]["ok"]
    assert any("信源在构建后发生变化" in r for r in g["reasons"])
    assert g["checks"]["sources"]["fingerprint"] != g["checks"]["sources"]["built_fingerprint"]


def test_gate_blocks_on_expired_or_future_docs():
    expired = _docs("a", expires_at="2026-01-01T00:00:00Z")
    g = gate.evaluate_gate(CFG, expired, _built(expired), now=NOW)
    assert g["checks"]["freshness"]["expired"] == 1 and not g["allowed"]
    pending = _docs("a", valid_from="2027-01-01T00:00:00Z")
    g = gate.evaluate_gate(CFG, pending, _built(pending), now=NOW)
    assert g["checks"]["freshness"]["not_yet_effective"] == 1 and not g["allowed"]


def test_gate_stale_ratio_threshold():
    old = "2025-01-01T00:00:00Z"
    docs = [{"doc_id": "d0", "created_at": "2026-11-01T00:00:00Z", "last_verified_at": old, "content_hash": "a"},
            {"doc_id": "d1", "created_at": "2026-11-01T00:00:00Z", "content_hash": "b"}]
    meta = _built(docs)
    loose = gate.evaluate_gate({**CFG, "wiki_stale_ratio_max": "0.6"}, docs, meta, now=NOW)
    assert loose["checks"]["freshness"]["stale_ratio"] == 0.5 and loose["allowed"]
    strict = gate.evaluate_gate(CFG, docs, meta, now=NOW)         # 阈值 0.1
    assert not strict["allowed"] and any("陈旧文档占比" in r for r in strict["reasons"])


def test_gate_tolerates_bad_threshold_value():
    docs = _docs("a")
    g = gate.evaluate_gate({**CFG, "wiki_stale_ratio_max": "abc"}, docs, _built(docs), now=NOW)
    assert g["checks"]["freshness"]["stale_ratio_max"] == 0.1 and g["allowed"]


def test_gate_without_docs_is_not_allowed():
    g = gate.evaluate_gate(CFG, [], _built([]), now=NOW)
    assert not g["allowed"] and not g["checks"]["sources"]["ok"]


# ---------------------------------------------------------------- 构建
CHUNKS = [
    {"chunk_id": "c1", "doc_id": "d1", "idx": 0, "title": "说明书",
     "text": "长按复位键 3 秒进入配网模式，手机连接设备热点。"},
    {"chunk_id": "c2", "doc_id": "d1", "idx": 1, "title": "说明书", "text": "配网失败请确认 2.4G 频段。"},
]


class FakeLLM:
    def __init__(self, text):
        self.text, self.calls, self.prompts = text, 0, []

    def complete(self, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        return self.text


def _good_payload() -> str:
    """模型输出的样例。用 json.dumps 构造：手拼的 JSON 结尾多一个 `]}` 就解析不出来（我第一版就拼错了）。"""
    return json.dumps({"pages": [
        {"title": "配网流程", "section": "业务流程",
         "markdown": "## 配网流程\n长按复位键进入配网模式。",
         "claims": [
             {"statement": "长按复位键 3 秒进入配网模式",
              "evidence": [{"chunk_id": "c1", "quote": "长按复位键 3 秒进入配网模式"}]},
             {"statement": "这条指向素材之外", "evidence": [{"chunk_id": "nope", "quote": "x"}]},
             {"statement": "这条原话对不上", "evidence": [{"chunk_id": "c2", "quote": "设备不支持 5G"}]},
         ]},
        {"title": "边界", "section": "安全边界", "markdown": "正文", "claims": []},
    ]}, ensure_ascii=False)


GOOD = _good_payload()


def test_build_keeps_only_claims_with_real_evidence():
    out = builder.build("智能设备排障", CHUNKS, FakeLLM(GOOD), batch_size=5)
    assert out["available"]
    assert len(out["pages"]) == 1                           # 第二页没有任何有证据的结论：整页丢弃
    page = out["pages"][0]
    assert page["section"] == "业务流程" and page["title"] == "配网流程"
    assert [c["statement"] for c in page["claims"]] == ["长按复位键 3 秒进入配网模式"]
    assert page["claims"][0]["evidence"] == [
        {"chunk_id": "c1", "doc_id": "d1", "idx": 0, "quote": "长按复位键 3 秒进入配网模式"}]
    assert page["claims"][0]["source_hash"] == gate.text_fingerprint(CHUNKS[0]["text"])
    s = out["stats"]
    assert s["pages_dropped"] == 1 and s["claims_dropped"] == 2 and s["evidence_dropped"] == 2
    assert s["sections_normalized"] == 1 and s["pages_kept"] == 1 and s["claims_kept"] == 1


def _one_claim(quote: str) -> str:
    """一条结论、一条证据，证据原话由参数指定。用 json.dumps 构造，不手拼转义。"""
    return json.dumps({"pages": [{
        "title": "配网流程", "section": "业务流程", "markdown": "## 配网流程\n长按复位键进入配网模式。",
        "claims": [{"statement": "长按复位键 3 秒进入配网模式",
                    "evidence": [{"chunk_id": "c1", "quote": quote}]}]}]}, ensure_ascii=False)


def test_build_quote_match_ignores_whitespace_but_not_characters():
    out = builder.build("智能设备排障", CHUNKS, FakeLLM(_one_claim("长按复位键3秒进入  配网模式")), batch_size=5)
    assert out["stats"]["claims_kept"] == 1                 # 只差空白：算原话
    out = builder.build("智能设备排障", CHUNKS, FakeLLM(_one_claim("长按复位键 5 秒进入配网模式")), batch_size=5)
    assert out["stats"]["claims_kept"] == 0                 # 改了字（3 → 5）：不算原话
    assert out["stats"]["evidence_dropped"] == 1 and out["stats"]["pages_dropped"] == 1


def test_build_dedupes_identical_chunks_and_counts_honestly():
    chunks = CHUNKS + [dict(CHUNKS[0], chunk_id="c3"), dict(CHUNKS[1], chunk_id="c4")]
    out = builder.build("场景", chunks, FakeLLM('{"pages":[]}'), batch_size=10)
    assert out["stats"]["chunks_deduped"] == 2 and out["stats"]["batches"] == 1
    assert out["stats"]["chunks_sent"] == 2
    assert out["available"] and out["pages"] == []         # 模型在干活，只是产出为空


def test_build_without_model_or_with_bad_json():
    assert builder.build("场景", CHUNKS, None)["available"] is False
    assert builder.build("场景", CHUNKS, None)["stats"]["batches"] == 0
    out = builder.build("场景", CHUNKS, FakeLLM("我不是 JSON"), batch_size=1)
    assert out["stats"]["failed_batches"] == 2 and not out["available"]
    # 失败原因要逐批记下来：实测第一版只给计数，看到「6 批失败 5 批」却不知道是超时还是被截断
    assert [f["batch"] for f in out["stats"]["failures"]] == [1, 2]
    assert "不是完整 JSON" in out["stats"]["failures"][0]["reason"]
    # 空输出 = 离线占位：第一批就停，后面的记为没送（skipped），不记成失败
    llm = FakeLLM("")
    out = builder.build("场景", CHUNKS, llm, batch_size=1)
    assert out["stats"] == {**out["stats"], "batches": 1, "failed_batches": 1, "batches_skipped": 1}
    assert llm.calls == 1 and not out["available"]
    assert "模型返回空" in out["stats"]["failures"][0]["reason"]


def test_build_reports_progress_per_batch():
    seen = []
    builder.build("场景", CHUNKS, FakeLLM('{"pages":[]}'), batch_size=1, on_batch=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 2), (2, 2)]


def test_build_prompt_bounds_output_size():
    """一批 12 段、不限输出时实测 6 批失败 5 批（截断 / 超时）。提示词必须给出明确的上限。"""
    llm = FakeLLM(GOOD)
    builder.build("场景", CHUNKS, llm, batch_size=6)
    p = llm.prompts[0]
    assert "本批最多 2 页" in p and "不超过 350 字" in p and "最多 4 条 claim" in p


def test_build_prompt_carries_scenario_and_instruction():
    llm = FakeLLM(GOOD)
    builder.build("订单排障", CHUNKS, llm, instruction="只提取退换货规则")
    assert "订单排障" in llm.prompts[0] and "只提取退换货规则" in llm.prompts[0]
    assert "chunk_id=c1" in llm.prompts[0]


def test_build_requires_scenario_and_chunks():
    assert builder.build("  ", CHUNKS, FakeLLM(GOOD))["available"] is False
    assert builder.build("场景", [], FakeLLM(GOOD))["available"] is False


# ---------------------------------------------------------------- 按场景筛信源
def test_select_by_scenario_keeps_only_chunks_with_discriminating_terms():
    """场景里的词要能在语料里区分出内容：「使用」几乎每段都有，指不出任何东西。"""
    from llmwiki import relevancy
    chunks = [{"chunk_id": "a", "text": "长按复位键3秒进入配网模式"},
              {"chunk_id": "b", "text": "配网失败请确认频段"},
              {"chunk_id": "c", "text": "报销流程：扫码后开具发票"}]
    chunks += [{"chunk_id": f"x{i}", "text": f"使用说明第{i}条：系统功能介绍"} for i in range(12)]
    r = relevancy.select_by_scenario("配网使用故障排查", chunks)
    assert r["mode"] == "scenario" and r["terms"] == ["配网"]
    assert [c["chunk_id"] for c in r["chunks"]] == ["a", "b"]     # 「使用」没能把 12 段说明书拖进来
    assert r["kept"] == 2 and r["total"] == len(chunks)


def test_select_by_scenario_falls_back_to_all_when_no_term_hits():
    from llmwiki import relevancy
    chunks = [{"chunk_id": "a", "text": "账号绑定说明"}]
    r = relevancy.select_by_scenario("量子计算入门", chunks)
    assert r["mode"] == "all" and r["chunks"] == chunks and "没有命中" in r["reason"]


def test_select_by_scenario_counts_df_over_distinct_texts():
    from llmwiki import relevancy
    chunks = [{"chunk_id": f"a{i}", "text": "配网步骤说明"} for i in range(10)]
    chunks.append({"chunk_id": "b", "text": "账号绑定说明"})
    # 「配网」只出现在一种文本里（哪怕被传了 10 遍），仍是强词
    r = relevancy.select_by_scenario("配网失败怎么办", chunks)
    assert r["terms"] == ["配网"] and r["kept"] == 10


# ---------------------------------------------------------------- 检索
def _wiki(chunks_text=None):
    pages = [{"id": "p1", "title": "配网流程", "section": "业务流程", "body_md": "长按复位键进入配网模式",
              "summary": "长按复位键进入配网模式"},
             {"id": "p2", "title": "发票开具", "section": "业务流程", "body_md": "扫码绑定企业账号后开具发票",
              "summary": "开具发票"}]
    texts = chunks_text or {c["chunk_id"]: c["text"] for c in CHUNKS}
    claims = [
        {"page_id": "p1", "statement": "长按复位键 3 秒进入配网模式",
         "evidence": [{"chunk_id": "c1", "quote": "长按复位键 3 秒进入配网模式"}],
         "source_hash": gate.text_fingerprint(texts["c1"])},
        {"page_id": "p1", "statement": "配网失败请确认 2.4G 频段",
         "evidence": [{"chunk_id": "c2", "quote": ""}],
         "source_hash": gate.text_fingerprint(texts["c2"])},
        {"page_id": "p2", "statement": "扫码绑定企业账号后可开具发票",
         "evidence": [{"chunk_id": "c2", "quote": ""}],
         "source_hash": gate.text_fingerprint(texts["c2"])},
    ]
    return pages, claims, texts


def test_search_returns_evidence_chunks_of_matched_claims():
    # 问句刻意不带「配网」：带了就会同时命中第二条结论（它的陈述里也有「配网」）
    cands, rep = retriever.search("复位键长按几秒", *_wiki())
    assert [c["chunk_id"] for c in cands] == ["c1"]          # 命中的是「复位键」那条结论
    assert cands[0]["pages"] == ["p1"] and rep["status"] == "ok" and rep["stale_claims"] == 0
    assert rep["pages"][0]["title"] == "配网流程" and rep["pages"][0]["claims_matched"] == 1


def test_search_question_mentioning_shared_word_matches_both_claims():
    """问句里有「配网」时，同一页的两条结论都会命中——这是词法匹配的正常行为，钉住它。"""
    cands, rep = retriever.search("复位键长按几秒进入配网", *_wiki())
    assert {c["chunk_id"] for c in cands} == {"c1", "c2"}
    assert rep["pages"][0]["claims_matched"] == 2


def test_search_page_match_bring_evidence_of_its_claims():
    cands, rep = retriever.search("配网流程是什么", *_wiki())
    assert {c["chunk_id"] for c in cands} == {"c1", "c2"}
    assert rep["pages"][0]["claims_used"] == 2


def test_search_ignores_claim_whose_source_text_changed():
    """原文改过了：指纹对不上，这条结论整条弃用，即使它的 chunk_id 还在。"""
    _, claims, _ = _wiki()
    pages = [{"id": "p1", "title": "配网", "section": "业务流程", "body_md": "配网", "summary": "配网"}]
    texts = {"c1": "长按复位键 5 秒进入配网模式", "c2": CHUNKS[1]["text"]}
    cands, rep = retriever.search("复位键长按几秒", pages, claims[:1], texts)
    assert cands == [] and rep["stale_claims"] == 1 and rep["status"] == "all_evidence_stale"


def test_search_evidence_chunk_deleted_is_stale():
    pages, claims, _ = _wiki()
    cands, rep = retriever.search("复位键长按几秒", pages, claims[:1], {})    # c1 已不存在
    assert cands == [] and rep["stale_claims"] == 1


def test_search_negative_cases():
    pages, claims, texts = _wiki()
    assert retriever.search("", pages, claims, texts)[1]["status"] == "empty_question"
    assert retriever.search("量子纠缠是什么", pages, claims, texts)[1]["status"] == "no_page"
