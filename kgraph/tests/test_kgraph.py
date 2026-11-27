"""kgraph 单测：只用假 store / 假模型，不需要真 Neo4j。

重点钉住的都是实测踩过的坑：
- 节点模式里的属性与列表参数比较恒不成立（查询静默返回 0 行）；
- 「提及」边不能做最长匹配剪枝，否则术语在图里却查不到分块；
- DF 必须按不同文本计；实体名额不能被高频泛词占满；
- LLM 生成的 Cypher 必须过只读校验，失败必须回退模板并留痕。
"""
from __future__ import annotations

import pytest

from kgraph import builder, contracts, cypher, extract_llm, retriever, terms

NS = contracts.GRAPH_NAMESPACE


# ---------------------------------------------------------------- 术语抽取
def test_df_counts_distinct_texts_not_chunks():
    """同一份文本传了三遍，DF 只能记 1——否则词频被放大 N 倍、阈值全部失真。"""
    chunks = [{"chunk_id": str(i), "text": "设备配网步骤与复位键说明"} for i in range(3)]
    chunks.append({"chunk_id": "9", "text": "配网失败后请长按复位键重试"})
    ents = {e["name"]: e["df"] for e in terms.select_entities(chunks)}
    assert ents["配网"] == 2          # 两种不同文本
    assert ents["复位键"] == 2


def test_term_in_single_text_is_not_an_entity():
    chunks = [{"chunk_id": "1", "text": "设备配网步骤"}, {"chunk_id": "2", "text": "配网失败重试"}]
    ents = {e["name"] for e in terms.select_entities(chunks)}
    assert "设备配网" not in ents     # 只出现一次，无法证明它是术语
    assert "配网" in ents


def test_longest_match_prunes_tail_with_same_df():
    """「配网模式」出现几次、「配网」就出现几次 → 「配网」只是更长术语的尾巴。"""
    chunks = [{"chunk_id": "1", "text": "进入配网模式的方法"}, {"chunk_id": "2", "text": "退出配网模式的方法"}]
    ents = {e["name"] for e in terms.select_entities(chunks)}
    assert "配网模式" in ents and "配网" not in ents


def test_entity_budget_does_not_starve_specific_terms():
    """名额被高频泛词占满、专门术语被截掉——实测导致「问配网一个实体都命中不了」。"""
    chunks = [{"chunk_id": f"g{i}", "text": f"说明文档模型数据检索{i}"} for i in range(30)]
    chunks += [{"chunk_id": "a", "text": "配网需要长按复位键"},
               {"chunk_id": "b", "text": "配网失败请长按复位键重试"},
               {"chunk_id": "c", "text": "配网完成后指示灯常亮"}]
    ents = {e["name"] for e in terms.select_entities(chunks)}
    assert "配网" in ents
    # 不强求「复位键」本身入选：两段文本里它都在「按复位键」里，按最长匹配会保留更长的那个
    assert any("复位键" in e for e in ents)


def test_two_char_terms_need_three_texts_but_longer_terms_need_two():
    """跨词二元组是主要噪声来源：问「账号绑定后能做什么」命中了「定后」（来自「绑定后」被切开），
    只因为它碰巧出现在两段文本里。二字词门槛抬到 3；三字以上保持 2。
    语料要够 5 种文本——太小的话会触发「小语料放宽」，门槛抬不起来。"""
    chunks = [{"chunk_id": "1", "text": "账号绑定后进入页面甲"},
              {"chunk_id": "2", "text": "账号绑定后可以上传产品文档"},
              {"chunk_id": "3", "text": "账号绑定需要扫码"},
              {"chunk_id": "4", "text": "说明书第一部分的内容"},
              {"chunk_id": "5", "text": "说明书第二部分的内容"}]
    ents = {e["name"] for e in terms.select_entities(chunks)}
    assert "账号绑定" in ents                            # 四字：出现两次就是证据
    assert any("绑定后" in e for e in ents)              # 「号绑定后」入选（比「绑定后」更长、DF 相同）
    assert "定后" not in ents                            # 二字跨词片段只出现两次：不够
    assert not {"号绑", "绑定", "绑定后"} & ents        # 被更长术语盖住的尾巴


def test_two_char_rule_relaxes_on_tiny_corpus():
    """只有两份文档时，二字词出现两次就得留着——否则规则互相打架，建出一张空图。"""
    chunks = [{"chunk_id": "1", "text": "设备配网步骤"}, {"chunk_id": "2", "text": "配网失败重试"}]
    assert "配网" in {e["name"] for e in terms.select_entities(chunks)}


def test_present_keeps_subsumed_but_prune_drops():
    names = ["配网模式", "配网"]
    text = "进入配网模式"
    assert terms.present(text, names) == ["配网模式", "配网"]       # 提及边：召回要全
    assert terms.prune_subsumed(terms.present(text, names)) == ["配网模式"]   # 共现边：要代表


def test_mentions_cap_and_sorting():
    names = ["甲乙", "丙丁", "戊己", "庚辛"]
    assert terms.mentions("甲乙丙丁戊己庚辛", names, max_per_chunk=2) == ["甲乙", "丙丁"]


def test_co_mentions_key_is_order_independent():
    co = terms.co_mentions([["A", "B"], ["B", "A"]])
    assert co == {("A", "B"): 2}


# ---------------------------------------------------------------- plan
def _chunks():
    return [
        {"chunk_id": "c1", "doc_id": "d1", "idx": 0, "text": "设备配网步骤：长按复位键3秒进入配网模式"},
        {"chunk_id": "c2", "doc_id": "d1", "idx": 1, "text": "配网失败了请确认频段，复位键长按三秒"},
        {"chunk_id": "c3", "doc_id": "d2", "idx": 0, "text": "账号绑定说明：扫码绑定企业账号"},
    ]


def test_plan_mentions_are_complete_and_weights_normalized():
    p = builder.plan(_chunks(), [{"doc_id": "d1", "title": "T"}, {"doc_id": "d2"}])
    names = {e["name"] for e in p["entities"]}
    assert {"配网", "复位键"} <= names
    mention_of = {m["name"] for m in p["mentions"] if m["chunk_id"] == "c1"}
    assert "配网" in mention_of                 # 一跳召回依赖它
    df = {e["name"]: e["df"] for e in p["entities"]}
    for e in p["co_mentions"]:
        # 共现次数最多是 min(df_a, df_b)，所以归一化后的上界是 sqrt(min/max)；
        # 等号成立的情形（每次出现都共现）也必须 ≤ 1
        a, b = df[e["a"]], df[e["b"]]
        assert e["w"] <= (min(a, b) / (a * b) ** 0.5) + 1e-9
        assert 0 < e["w"] <= 1.0
    assert p["stats"]["mentions"] == len(p["mentions"])
    assert not p["typed_rels"]


def test_plan_merges_llm_extra_relations():
    extra = {"entities": [{"name": "手机", "kind": "设备"}],
             "mentions": [{"chunk_id": "c2", "entities": ["手机"]}],
             "relations": [{"src": "复位键", "dst": "配网", "type": "长按进入", "chunk_id": "c1"}]}
    p = builder.plan(_chunks(), [{"doc_id": "d1", "title": "T"}, {"doc_id": "d2"}], llm_extra=extra)
    assert p["typed_rels"] == [extra["relations"][0]]
    assert {"name": "手机", "kind": "设备"} in p["entities"] or any(
        e["name"] == "手机" and e["kind"] == "设备" for e in p["entities"])
    assert {"chunk_id": "c2", "name": "手机"} in p["llm_mentions"]


# ---------------------------------------------------------------- Cypher 防护
@pytest.mark.parametrize("bad,why", [
    ("MATCH (n) DETACH DELETE n $kb", "写操作关键词"),
    ("MATCH (n) SET n.x = 1 RETURN n.x", "写操作关键词"),
    ("MERGE (n:Entity {kb: $kb}) RETURN n", "写操作关键词"),
    ("LOAD CSV WITH HEADERS FROM 'x' AS r RETURN r", "写操作关键词"),
    ("CALL db.index.fulltext.queryNodes('i','q') YIELD node RETURN node", "写操作关键词"),
    ("MATCH (n {kb: $kb}) RETURN n; MATCH (n {kb: $kb}) RETURN n", "单条语句"),
    ("MATCH (n:Entity) RETURN n.name LIMIT 5", "命名空间"),
    ("MATCH (n {kb: $kb}) WITH n", "RETURN"),
    ("", "为空"),
])
def test_write_or_unsafe_cypher_rejected(bad, why):
    with pytest.raises(ValueError, match=why):
        cypher.validate_readonly(bad)


def test_readonly_cypher_accepted_and_trimmed():
    q = cypher.validate_readonly("MATCH (e:Entity {kb: $kb}) WHERE e.name IN $names RETURN e.name LIMIT 5;\n")
    assert q.endswith("LIMIT 5")


def test_offset_is_not_mistaken_for_set():
    """词边界：OFFSET 里的 SET 不能被当成写操作。"""
    q = "MATCH (e:Entity {kb: $kb}) RETURN e.name SKIP 1 OFFSET 2"
    assert cypher.validate_readonly(q) == q


def test_ensure_limit_adds_and_clamps():
    q = "MATCH (e:Entity {kb: $kb}) RETURN e.name"
    assert cypher.ensure_limit(q, 50).endswith("LIMIT 50")
    assert cypher.ensure_limit(q + " LIMIT 999", 50).endswith("LIMIT 50")
    assert cypher.ensure_limit(q + " limit 10", 50).endswith("limit 10")


def test_extract_cypher_from_fenced_output():
    raw = '好的，查询如下：\n```cypher\nMATCH (e:Entity {kb: $kb}) RETURN e.name;\n```\n以上。'
    assert cypher.extract_cypher(raw) == "MATCH (e:Entity {kb: $kb}) RETURN e.name"
    with pytest.raises(ValueError):
        cypher.extract_cypher("抱歉，我无法生成查询。")


def test_templates_use_in_predicate_not_list_in_pattern():
    """回归：`{name: $names}` 在 Neo4j 5.26 里恒不成立，查询静默返回 0 行；
    放进 NOT (...) 里更会变成空真。模板里必须全部用 IN 谓词。"""
    (_, hop1), (_, hop2) = cypher.templates(2, typed=True)
    assert "name: $names" not in hop1 + hop2
    assert "WHERE e.name IN $names" in hop1 and "m.name IN $names" in hop2
    assert "CO_MENTION|REL*1..1" in hop2          # hops=2 → 实体之间最多 1 条边
    assert "CO_MENTION*1..1" in cypher.templates(2, typed=False)[1][1]   # 没有语义边就不带 REL


def test_no_rel_warning_pattern_when_no_typed_relations():
    """图里没有语义边时不能去匹配 REL 类型——否则 Neo4j 每次查询都发「类型不存在」的警告。"""
    st = FakeStore(entities=["配网"], hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1}])
    _, rep = retriever.search("配网失败", st, hops=2)
    assert rep["typed_rels"] is False
    assert any("REL" in q for q in st.reads if "length(p)" in q) is False


@pytest.mark.parametrize("hops", [0, 4, "2", 2.5])
def test_templates_reject_bad_hops(hops):
    with pytest.raises(ValueError):
        cypher.templates(hops)


def test_prompt_uses_in_predicate_in_examples():
    p = cypher.build_prompt("配网失败怎么办", hops=2)
    assert "IN $names" in p and "{name: $names}" in p     # 明确告诉模型不要那样写
    assert "配网失败怎么办" in p


# ---------------------------------------------------------------- 检索器
class FakeStore:
    def __init__(self, entities=(), hop1=(), hop2=(), llm_rows=None):
        self.namespace = NS
        self.entities, self.hop1, self.hop2 = list(entities), list(hop1), list(hop2)
        self.llm_rows = llm_rows or []
        self.reads: list[str] = []
        self.explained: list[str] = []

    def read(self, query, params=None):
        self.reads.append(query)
        if "RETURN e.name AS name" in query:
            return [{"name": n} for n in self.entities]
        if "db.relationshipTypes" in query:
            return [{"t": "CO_MENTION"}]
        if "length(p) + 1 AS hop" in query:
            return self.hop2
        if "1 AS hop" in query:
            return self.hop1
        return self.llm_rows

    def explain(self, query, params=None):
        self.explained.append(query)


class FakeLLM:
    def __init__(self, text=""):
        self.text, self.calls = text, 0

    def complete(self, prompt, **kw):
        self.calls += 1
        return self.text


def test_match_entities_longest_first_and_capped():
    names = ["配网", "配网模式", "复位键", "设备", "说明", "步骤"]
    assert retriever.match_entities("配网模式失败", names) == ["配网模式"]     # 「配网」被包含
    assert len(retriever.match_entities("复位键设备说明步骤", names)) == 3    # 上限 3


def test_search_without_matched_entity_makes_no_graph_query():
    st = FakeStore(entities=["配网"])
    cands, rep = retriever.search("量子纠缠是什么", st)
    assert cands == [] and rep["status"] == "no_entity"
    assert len(st.reads) == 1                # 只读了一次实体表，没有多余查询


def test_search_template_path_and_subgraph():
    st = FakeStore(
        entities=["配网", "复位键"],
        hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1},
              {"chunk_id": "c2", "matched": ["配网"], "path": [], "hop": 1}],
        hop2=[{"chunk_id": "c3", "path": ["配网", "复位键"], "hop": 2}])
    cands, rep = retriever.search("配网失败了怎么办", st, hops=2)
    assert [(c.chunk_id, c.hop) for c in cands] == [("c1", 1), ("c2", 1), ("c3", 2)]
    assert cands[0].score == 1.0 and 0 < cands[2].score < 0.7
    assert rep["mode"] == "template" and rep["llm_calls"] == 0
    assert rep["matched_entities"] == ["配网"]
    assert rep["candidates"]["by_hop"] == {"1": 2, "2": 1}
    sg = rep["subgraph"]
    ids = {n["id"] for n in sg["nodes"]}
    assert {"e:配网", "e:复位键", "chunk:c3", "chunk:c1"} <= ids
    assert {"from": "e:复位键", "to": "chunk:c3", "type": "提及"} in sg["edges"]


def test_search_keeps_shortest_hop_per_chunk():
    st = FakeStore(entities=["配网"],
                   hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1}],
                   hop2=[{"chunk_id": "c1", "path": ["配网", "复位键"], "hop": 2}])
    cands, _ = retriever.search("配网失败", st)
    assert len(cands) == 1 and cands[0].hop == 1


@pytest.mark.parametrize("bad_query,why", [
    ("MATCH (n {kb: $kb}) DETACH DELETE n", "写操作关键词"),
    ("MATCH (n:Entity) WHERE e.name IN $names RETURN e.name LIMIT 5", "命名空间"),
])
def test_llm_cypher_falls_back_to_template_with_reason(bad_query, why):
    """模型生成的语句过不了校验 → 回退模板，并把原因写进报告。这一路不能带崩检索。"""
    st = FakeStore(entities=["配网"], hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1}])
    llm = FakeLLM("```cypher\n" + bad_query + "\n```")
    cands, rep = retriever.search("配网失败", st, mode="llm", llm=llm)
    assert llm.calls == 1
    assert rep["mode"] == "template(after_llm_fallback)"
    assert why in rep["fallback_reason"]
    assert [c.chunk_id for c in cands] == ["c1"] and rep["llm_calls"] == 0
    assert not st.explained                # 没过校验的语句不会走到 EXPLAIN


def test_llm_cypher_without_chunk_id_column_falls_back():
    """没有 chunk_id 就回不到 PG 的分块，结果不可用——必须回退而不是返回一堆查不动的行。"""
    st = FakeStore(entities=["配网"], llm_rows=[{"n": 3}],
                   hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1}])
    q = "MATCH (e:Entity {kb: $kb}) RETURN count(e) AS n"
    cands, rep = retriever.search("配网失败", st, mode="llm", llm=FakeLLM(q))
    assert "chunk_id" in rep["fallback_reason"]
    assert [c.chunk_id for c in cands] == ["c1"]
    # 假 store 收到的是裸语句（EXPLAIN 前缀由真 store 的 explain() 加）；通过校验 → 确实走到预检
    assert st.explained == [q + " LIMIT 50"]


def test_llm_cypher_empty_result_falls_back():
    st = FakeStore(entities=["配网"], hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1}])
    cands, rep = retriever.search("配网失败", st, mode="llm", llm=FakeLLM("MATCH (e:Entity {kb: $kb}) RETURN 1 AS x LIMIT 5"))
    assert "0 行" in rep["fallback_reason"] or "chunk_id" in rep["fallback_reason"]
    assert cands


def test_llm_cypher_used_when_valid():
    good = "MATCH (e:Entity {kb: $kb}) WHERE e.name IN $names MATCH (e)<-[:MENTIONS]-(c:Chunk {kb: $kb}) RETURN c.chunk_id AS chunk_id LIMIT 20"
    st = FakeStore(entities=["配网"], llm_rows=[{"chunk_id": "c9"}])
    cands, rep = retriever.search("配网失败", st, mode="llm", llm=FakeLLM(good))
    assert rep["mode"] == "llm" and rep["fallback_reason"] is None
    assert [c.chunk_id for c in cands] == ["c9"]
    assert st.explained == [good]                 # 执行前确实先让 Neo4j 编译过（EXPLAIN 由真 store 加前缀）


def test_llm_mode_without_model_falls_back():
    st = FakeStore(entities=["配网"], hop1=[{"chunk_id": "c1", "matched": ["配网"], "path": [], "hop": 1}])
    _, rep = retriever.search("配网失败", st, mode="llm", llm=None)
    assert "llm_unavailable" in rep["fallback_reason"]


def test_search_rejects_bad_hops():
    with pytest.raises(ValueError):
        retriever.search("x", FakeStore(), hops=9)


# ---------------------------------------------------------------- LLM 抽取
def test_extract_llm_drops_hallucinated_entities_and_dangling_relations():
    chunks = [{"chunk_id": "c1", "doc_id": "d", "idx": 0, "text": "长按复位键3秒进入配网模式"}]
    raw = ('{"chunks":[{"index":0,"entities":[{"name":"复位键","kind":"部件"},'
           '{"name":"蓝牙模块"},{"name":"配网模式"}],'
           '"relations":[{"src":"复位键","dst":"配网模式","type":"长按进入"},'
           '{"src":"复位键","dst":"蓝牙模块","type":"导致"}]}]}')
    out = extract_llm.extract_llm(chunks, FakeLLM(raw), batch_size=4, cap=10)
    names = {e["name"] for e in out["entities"]}
    assert names == {"复位键", "配网模式"}                 # 原文里没有「蓝牙模块」：幻觉，拦下
    assert out["relations"] == [{"src": "复位键", "dst": "配网模式", "type": "长按进入", "chunk_id": "c1"}]
    assert out["stats"]["dropped"] == 2 and out["stats"]["batches"] == 1


def test_extract_llm_handles_bad_json_and_offline():
    chunks = [{"chunk_id": "c1", "doc_id": "d", "idx": 0, "text": "随便一段"}]
    out = extract_llm.extract_llm(chunks, FakeLLM("这不是 JSON"), batch_size=4)
    assert out["stats"]["failed_batches"] == 1 and not out["relations"]
    assert extract_llm.extract_llm(chunks, None)["stats"]["available"] is False


def test_extract_llm_dedupes_and_caps_chunks():
    chunks = [{"chunk_id": str(i), "doc_id": "d", "idx": i, "text": "设备配网说明"} for i in range(5)]
    chunks += [{"chunk_id": "x", "doc_id": "d", "idx": 9, "text": "账号绑定说明"}]
    llm = FakeLLM('{"chunks":[]}')
    out = extract_llm.extract_llm(chunks, llm, batch_size=1, cap=1)
    assert out["stats"]["chunks_deduped"] == 4          # 5 条同文只送 1 条
    assert out["stats"]["chunks_sent"] == 1 and out["stats"]["chunks_skipped"] == 1
    assert llm.calls == 1
