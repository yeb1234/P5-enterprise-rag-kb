"""Wiki 检索：问题 → 页面 → 命中的结论 → 证据分块。

## Wiki 这一路往融合里送的是什么

送的是**证据分块**，不是页面正文。原因与项目的硬约束一致：最终进模型、进来源卡片的
必须是 PG 里真实存在、且文档仍 ready 的分块。页面正文与命中的结论放在路由报告里，
供检索调试页展示「Wiki 是凭什么把这几段原文找出来的」——这就是证据链的可视化。

## 证据会过期

构建时每条结论记了一个指纹（它引用的全部分块正文合并后的哈希）。检索时重算一遍：
对不上，说明原文被改过或有分块被删了，这条结论不再可信，**整条弃用**并计数。
"""
from __future__ import annotations

import re

from llmwiki.gate import text_fingerprint

_EN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def terms(text: str) -> set[str]:
    """与向量路由的词法门禁同一套分词口径：英文/数字整词 + 中文相邻二元组。

    两路用同一种分词是为了命中数可比；不共用实现是为了本包不依赖 webapp/。
    """
    text = (text or "").lower()
    out = {m for m in _EN.findall(text) if len(m) > 1}
    cjk = _CJK.findall(text)
    if len(cjk) == 1:
        out.add(cjk[0])
    for i in range(len(cjk) - 1):
        out.add(cjk[i] + cjk[i + 1])
    return out


def search(question: str, pages: list[dict], claims: list[dict], chunk_index: dict[str, str],
           *, top_pages: int = 3) -> tuple[list[dict], dict]:
    """返回 (候选分块, 路由报告)。

    pages:  [{id, title, section, body_md, summary}]
    claims: [{page_id, statement, evidence: [{chunk_id, quote}], source_hash}]
    chunk_index: {chunk_id: 当前正文}——只有仍存在的 ready 分块才在这里。
    """
    qt = terms(question)
    report: dict = {"status": "ok", "pages": [], "claims_total": len(claims),
                    "stale_claims": 0, "evidence_chunks": 0}
    if not qt:
        report["status"] = "empty_question"
        return [], report

    claims_by_page: dict[str, list[dict]] = {}
    for c in claims:
        claims_by_page.setdefault(c.get("page_id"), []).append(c)

    scored: list[tuple[int, dict, list[dict]]] = []
    for p in pages:
        # 标题和目录分组权重更高：它们是人读目录时最先看的两样东西
        s = (3 * len(qt & terms(p.get("title") or ""))
             + 2 * len(qt & terms(p.get("section") or ""))
             + len(qt & terms(p.get("body_md") or "")))
        matched = [c for c in claims_by_page.get(p.get("id"), [])
                   if qt & terms(c.get("statement") or "")]
        s += 2 * len(matched)
        if s > 0:
            scored.append((s, p, matched))
    if not scored:
        report["status"] = "no_page"
        return [], report

    scored.sort(key=lambda x: (-x[0], x[1].get("id") or ""))
    top = scored[:top_pages]
    top_score = float(top[0][0])
    cands: dict[str, dict] = {}
    for s, p, matched in top:
        norm = round(s / top_score, 4)
        # 问题命中了具体结论就用那些结论；只命中页面（比如问得比较泛）就用该页全部结论
        usable = matched or claims_by_page.get(p.get("id"), [])
        kept = 0
        for c in usable:
            texts = [chunk_index.get(e.get("chunk_id")) for e in c.get("evidence") or []]
            if (not texts or any(t is None for t in texts)
                    or text_fingerprint("\n".join(texts)) != c.get("source_hash")):
                report["stale_claims"] += 1
                continue
            kept += 1
            for e in c["evidence"]:
                entry = cands.setdefault(
                    e["chunk_id"], {"chunk_id": e["chunk_id"], "score": 0.0, "pages": [],
                                    "statements": []})
                entry["score"] = max(entry["score"], round(0.8 * norm, 4))
                if p["id"] not in entry["pages"]:
                    entry["pages"].append(p["id"])
                if c["statement"] not in entry["statements"]:
                    entry["statements"].append(c["statement"])
        report["pages"].append({
            "page_id": p["id"], "title": p.get("title"), "section": p.get("section"),
            "score": norm, "claims_matched": len(matched), "claims_used": kept,
            "excerpt": (p.get("summary") or "")[:160],
        })

    out = sorted(cands.values(), key=lambda x: (-x["score"], x["chunk_id"]))
    report["evidence_chunks"] = len(out)
    report["candidates"] = len(out)
    if not out:
        report["status"] = "all_evidence_stale" if report["stale_claims"] else "no_page"
    return out, report
