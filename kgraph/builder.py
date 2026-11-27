"""建图：先从 PG 分块算出节点与边（plan，纯函数），再写进图库（apply）。

## 两步分开的原因

`plan` 不碰数据库，输入是 PG 里现成的分块和文档行，输出是节点/边的清单——
所以「抽词对不对、共现算得对不对」可以用单测直接钉住，不需要真 Neo4j。
`apply` 只负责把清单落库。

## 增量与全量

- **节点**按文档增量：指定 `doc_ids` 时只重写这些文档的 Document / Chunk 节点及其挂接边。
- **实体之间的边永远全量重算**：共现权重是跨文档的全局量，文档 A 变了会影响
  A 与 B 共同术语之间的边权。演示规模（几百个术语）下全量重算是毫秒级，
  到十万级术语时才需要改成局部更新。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from kgraph import terms


def plan(chunks: list[dict], docs: list[dict], *, llm_extra: dict | None = None) -> dict:
    """算出要写入的节点与边。

    chunks: [{chunk_id, doc_id, idx, text}]；docs: [{doc_id, title}]。
    llm_extra: extract_llm() 的产出（可选），用来叠加带类型的语义关系。
    """
    ents = terms.select_entities(chunks)
    dfmap = {e["name"]: int(e["df"]) for e in ents}
    names = list(dfmap)
    # 两套提及，各司其职：
    #   present  → 「提及」边：不剪，保证一跳召回完整（见图里「配网」查不到分块的坑）；
    #   mentions → 共现边：最长匹配后的代表术语，保证关系质量。
    per_chunk = {c["chunk_id"]: terms.present(c.get("text") or "", names) for c in chunks}
    co = terms.co_mentions([terms.prune_subsumed(ms, 16) for ms in per_chunk.values()])

    entities: dict[str, dict] = {e["name"]: {"name": e["name"], "kind": "term", "df": e["df"]}
                                 for e in ents}
    # 边权归一化：共现次数 / sqrt(df_a × df_b)。不归一化时，「文档」这种几乎每段都在的
    # 泛词会与所有术语都有很高的共现权，多跳就全从它这种枢纽走，等于没走关系。
    # 归一化后，两个专门术语只要共现过，权重就高于「专门术语 × 泛词」。
    edges = [{"a": a, "b": b, "w": round(w / ((dfmap[a] * dfmap[b]) ** 0.5), 4)}
             for (a, b), w in sorted(co.items())]
    typed: list[dict] = []
    llm_mentions: list[dict] = []
    if llm_extra:
        for e in llm_extra.get("entities", []):
            node = entities.setdefault(
                e["name"], {"name": e["name"], "kind": e.get("kind") or "entity", "df": 1})
            if node["kind"] == "term" and e.get("kind"):
                node["kind"] = e["kind"]
        typed = [dict(r) for r in llm_extra.get("relations", [])]
        for m in llm_extra.get("mentions", []):
            # 只补离线抽取漏掉的 MENTIONS 边，已有的不重复
            extra = [x for x in m.get("entities", [])
                     if x not in per_chunk.get(m.get("chunk_id"), [])]
            if extra:
                llm_mentions.append({"chunk_id": m["chunk_id"], "entities": extra})

    doc_ids = {d["doc_id"] for d in docs}
    owned = [c for c in chunks if c.get("doc_id") in doc_ids]
    doc_title = {d["doc_id"]: d.get("title") or "" for d in docs}
    out = {
        "documents": [{"doc_id": d, "title": doc_title[d]} for d in sorted(doc_ids)],
        "chunks": [{"chunk_id": c["chunk_id"], "doc_id": c["doc_id"],
                    "idx": int(c.get("idx") or 0)} for c in owned],
        "entities": sorted(entities.values(), key=lambda x: x["name"]),
        "mentions": [{"chunk_id": cid, "name": n} for cid, ms in per_chunk.items() for n in ms],
        "llm_mentions": [{"chunk_id": m["chunk_id"], "name": n}
                         for m in llm_mentions for n in m["entities"]],
        "co_mentions": edges,
        "typed_rels": typed,
        "stats": {
            "docs": len(doc_ids), "chunks": len(owned), "entities": len(entities),
            "mentions": sum(len(v) for v in per_chunk.values()),
            "co_mentions": len(edges), "typed_rels": len(typed),
        },
    }
    return out


def _unwind(store, statement: str, items: list[dict], *, batch: int = 500) -> None:
    """分批 UNWIND 写入。空清单直接跳过，不往图库发空查询。"""
    for i in range(0, len(items), batch):
        store.run("UNWIND $items AS item " + statement, {"kb": store.namespace, "items": items[i:i + batch]})


def apply(store, p: dict, *, doc_ids: list[str] | None = None) -> dict:
    """把 plan 的结果写进图库。`doc_ids` 给了就只重写这些文档，否则全量重建。"""
    ns = store.namespace
    store.ensure_indexes()

    if doc_ids:
        for did in doc_ids:
            store.run("MATCH (c:Chunk {kb: $kb, doc_id: $did}) DETACH DELETE c", {"kb": ns, "did": did})
            store.run("MATCH (d:Document {kb: $kb, doc_id: $did}) DETACH DELETE d", {"kb": ns, "did": did})
        # 已经没有任何分块提到的术语不再属于这张图
        store.run("MATCH (e:Entity {kb: $kb}) WHERE NOT (e)<-[:MENTIONS]-() DETACH DELETE e", {"kb": ns})
    else:
        store.run("MATCH (n {kb: $kb}) DETACH DELETE n", {"kb": ns})

    # 实体间的边永远全量重算。用有向模式匹配：每条边只被访问一次，避免删两次。
    store.run("MATCH (a:Entity {kb: $kb})-[r:CO_MENTION|REL]->(b:Entity {kb: $kb}) DELETE r", {"kb": ns})

    scope = set(doc_ids) if doc_ids else None
    docs = [d for d in p["documents"] if scope is None or d["doc_id"] in scope]
    chunks = [c for c in p["chunks"] if scope is None or c["doc_id"] in scope]
    chunk_ids = {c["chunk_id"] for c in chunks}

    _unwind(store, "MERGE (d:Document {kb: $kb, doc_id: item.doc_id}) SET d.title = item.title", docs)
    _unwind(store, "MERGE (c:Chunk {kb: $kb, chunk_id: item.chunk_id}) "
                   "SET c.doc_id = item.doc_id, c.idx = item.idx", chunks)
    _unwind(store, "MERGE (e:Entity {kb: $kb, name: item.name}) SET e.kind = item.kind, e.df = item.df",
            p["entities"])
    _unwind(store, "MATCH (d:Document {kb: $kb, doc_id: item.doc_id}), (c:Chunk {kb: $kb, chunk_id: item.chunk_id}) "
                   "MERGE (d)-[:HAS_CHUNK]->(c)", chunks)
    _unwind(store, "MATCH (c:Chunk {kb: $kb, chunk_id: item.chunk_id}), (e:Entity {kb: $kb, name: item.name}) "
                   "MERGE (c)-[:MENTIONS]->(e)",
            [m for m in p["mentions"] + p["llm_mentions"] if m["chunk_id"] in chunk_ids])
    _unwind(store, "MATCH (a:Entity {kb: $kb, name: item.a}), (b:Entity {kb: $kb, name: item.b}) "
                   "MERGE (a)-[r:CO_MENTION]->(b) SET r.weight = item.w", p["co_mentions"])
    _unwind(store, "MATCH (a:Entity {kb: $kb, name: item.src}), (b:Entity {kb: $kb, name: item.dst}) "
                   "MERGE (a)-[r:REL {kind: item.type}]->(b)", p["typed_rels"])

    nodes: Counter = Counter()
    for r in store.read("MATCH (n {kb: $kb}) RETURN labels(n)[0] AS label, count(*) AS c", {"kb": ns}):
        nodes[r["label"]] = int(r["c"])
    rels: Counter = Counter()
    for r in store.read("MATCH (a {kb: $kb})-[r]->(b {kb: $kb}) RETURN type(r) AS type, count(*) AS c", {"kb": ns}):
        rels[r["type"]] = int(r["c"])
    return {"mode": "incremental" if doc_ids else "full", "doc_ids": doc_ids,
            "nodes": dict(nodes), "rels": dict(rels), "plan_stats": p["stats"]}


def counts(store) -> dict[str, Any]:
    """图库现状，给状态接口用：各标签节点数、各类型边数。"""
    ns = store.namespace
    nodes = {r["label"]: int(r["c"]) for r in store.read(
        "MATCH (n {kb: $kb}) RETURN labels(n)[0] AS label, count(*) AS c", {"kb": ns})}
    rels = {r["type"]: int(r["c"]) for r in store.read(
        "MATCH (a {kb: $kb})-[r]->(b {kb: $kb}) RETURN type(r) AS type, count(*) AS c", {"kb": ns})}
    return {"nodes": nodes, "rels": rels}
