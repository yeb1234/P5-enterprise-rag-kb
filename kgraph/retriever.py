"""图谱检索：问题 → 命中实体 → 多跳扩展 → 候选分块。

## 流程

1. **找命中实体**：问题里出现了图里的哪些术语。做最长匹配——问「配网模式」时，
   「配网」不再单独算一次命中。
2. **查图**：两种方式
   - `template`（默认）：手写 Cypher 模板，一跳 + 多跳扩展，确定性、零模型调用；
   - `llm`：自己写的 Text2Cypher，模型生成 Cypher → 三道防护 → 执行；任何一步失败
     **回退模板并留痕**（`fallback_reason`），绝不让这一路把整个检索带崩。
3. **回 PG 复核**在适配层做（`webapp/kb_routes.py`）：图里只有 chunk_id，
   分块可能已被删除或文档已不再 ready。
"""
from __future__ import annotations

from kgraph import cypher
from kgraph.contracts import HOP_SCORE, GraphCandidate


def match_entities(question: str, names: list[str], *, limit: int = 3) -> list[str]:
    """问题里命中的实体名：最长匹配，再按「长词优先」取前 limit 个。

    limit 默认 3：问句里堆七八个短词时，每个都会沿图扩一大圈，结果被噪声淹没。
    长词更具体（「多租户」比「租户」带的信息多），所以先留长的。
    """
    q = (question or "").lower()
    hit = [n for n in names if n.lower() in q]
    hit.sort(key=len, reverse=True)
    out: list[str] = []
    for n in hit:
        ln = n.lower()
        if any(ln in o.lower() for o in out):
            continue
        out.append(n)
    return out[:limit]


def _text2cypher(question: str, store, llm, params: dict) -> str:
    """模型生成 → 校验 → 补 LIMIT → EXPLAIN 预检。任何一步不过都抛异常交给调用方回退。"""
    if llm is None:
        raise RuntimeError("llm_unavailable")
    raw = llm.complete(cypher.build_prompt(question, hops=int(params.get("hops") or 2)),
                       system="你是 Cypher 查询生成器，只输出一条只读 Cypher 语句。",
                       max_tokens=400, temperature=0.0)
    if not (raw or "").strip():
        raise RuntimeError("llm_unavailable")
    q = cypher.ensure_limit(cypher.validate_readonly(cypher.extract_cypher(raw)))
    store.explain(q, params)
    return q


def subgraph(cands: list[GraphCandidate], *, max_nodes: int = 24) -> dict:
    """给前端画图用的子图：实体节点 + 命中的分块节点 + 它们之间的边。"""
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()

    def add(key: str, label: str, kind: str) -> None:
        if key in nodes or len(nodes) < max_nodes:
            nodes[key] = {"id": key, "label": label, "kind": kind}

    for c in cands:
        ck = f"chunk:{c.chunk_id}"
        seq = list(c.path)
        for name in list(c.matched) + seq:
            add(f"e:{name}", name, "entity")
        for a, b in zip(seq, seq[1:]):
            edges.add((f"e:{a}", f"e:{b}", "关联"))
        if seq:
            edges.add((f"e:{seq[-1]}", ck, "提及"))
        for name in (c.matched if not seq else []):
            edges.add((f"e:{name}", ck, "提及"))
        add(ck, "分块 " + c.chunk_id[:8], "chunk")
    return {
        "nodes": list(nodes.values()),
        "edges": [{"from": a, "to": b, "type": t} for a, b, t in sorted(edges)
                  if a in nodes and b in nodes],
    }


def _has_typed(store) -> bool:
    """图里有没有带类型的语义边。问 schema 而不是直接 MATCH `REL`——
    直接匹配一个不存在的类型，Neo4j 又会发那条「类型不存在」的警告。"""
    rows = store.read("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS t")
    return any(r.get("t") == "REL" for r in rows)


def search(question: str, store, *, hops: int = 2, mode: str = "template",
            llm=None, top_n: int = 5) -> tuple[list[GraphCandidate], dict]:
    """返回 (候选分块, 路由报告)。报告里的每个字段都能直接进检索调试页。"""
    if hops not in HOP_SCORE:
        raise ValueError(f"hops 只支持 {sorted(HOP_SCORE)}，收到 {hops!r}")
    ns = store.namespace
    report: dict = {
        "status": "ok", "hops": hops, "requested_mode": mode, "mode": None,
        "fallback_reason": None, "matched_entities": [], "llm_calls": 0, "cypher": [],
    }
    names = [r["name"] for r in store.read(
        "MATCH (e:Entity {kb: $kb}) RETURN e.name AS name", {"kb": ns})]
    report["entities_total"] = len(names)
    matched = match_entities(question, names)
    report["matched_entities"] = matched
    if not matched:
        # 问题里一个图内术语都没有：这一路给不出线索，如实报告而不是硬凑
        report["status"] = "no_entity"
        return [], report

    params = {"kb": ns, "names": matched, "hops": hops}
    rows: list[dict] = []
    if mode == "llm":
        report["llm_calls"] = 1
        try:
            q = _text2cypher(question, store, llm, params)
            got = store.read(q, params)
            if not got:
                raise ValueError("模型生成的查询返回 0 行")
            if "chunk_id" not in got[0]:
                raise ValueError("结果缺少 chunk_id 列，无法回溯到 PG 分块")
            rows = got
            report["mode"] = "llm"
            report["cypher"].append({"name": "llm", "cypher": q})
        except Exception as exc:  # noqa: BLE001 - 回退模板，原因必须写进报告
            rows = []
            report["fallback_reason"] = f"{type(exc).__name__}: {str(exc)[:140]}"
    if not rows:
        if mode == "llm":
            report["llm_calls"] = 0          # 回退后实际没有产生可用结果的那次调用不计入
        report["mode"] = "template" if mode != "llm" else "template(after_llm_fallback)"
        typed = hops >= 2 and _has_typed(store)
        report["typed_rels"] = typed
        for name, q in cypher.templates(hops, typed=typed):
            report["cypher"].append({"name": name, "cypher": q})
            rows += store.read(q, params)

    # 同一个分块可能被多条路径命中，取最短跳数
    best: dict[str, dict] = {}
    for r in rows:
        cid = r.get("chunk_id")
        if not cid:
            continue
        hop = min(max(int(r.get("hop") or 1), 1), max(HOP_SCORE))
        cur = best.get(cid)
        if cur is None or hop < cur["hop"]:
            best[cid] = {"hop": hop, "path": list(r.get("path") or []),
                         "matched": list(r.get("matched") or matched[:4])}

    cands = []
    for cid, b in best.items():
        score = min(1.0, HOP_SCORE[b["hop"]] + 0.02 * min(len(b["matched"]), 5))
        cands.append(GraphCandidate(cid, round(score, 4), b["hop"],
                                    tuple(b["path"]), tuple(b["matched"])))
    cands.sort(key=lambda c: (c.hop, -c.score, c.chunk_id))
    # 每一跳各取前 top_n：一跳的不会被二跳的挤掉，二跳的也总有名额
    limited: list[GraphCandidate] = []
    for hop in sorted({c.hop for c in cands}):
        limited += [c for c in cands if c.hop == hop][:top_n]
    report["candidates"] = {"total": len(best), "returned": len(limited),
                            "by_hop": {str(h): sum(1 for c in limited if c.hop == h)
                                       for h in sorted({c.hop for c in limited})}}
    report["subgraph"] = subgraph(limited)
    return limited, report
