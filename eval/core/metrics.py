"""指标计算：区分「离线可计算的链路指标」与「接入后才可比较的质量指标」。"""
from __future__ import annotations

from .adapters import CITATION


def _norm(text: str) -> str:
    return "".join((text or "").split()).lower()


def resolve_gold_chunks(gold_evidence: list[str], chunks: list[dict]) -> list[str]:
    """金标用证据原文片段，运行时映射到 chunk_id；分块参数变化不会让标注失效。"""
    found = []
    for evidence in gold_evidence or []:
        needle = _norm(evidence)
        for c in chunks:
            if needle and needle in _norm(c["content"]):
                if c["chunk_id"] not in found:
                    found.append(c["chunk_id"])
                break
    return found


def retrieval_metrics(gold_evidence, chunks, retrieved, top_k: int) -> dict:
    gold_ids = resolve_gold_chunks(gold_evidence, chunks)
    ranked = [r["chunk_id"] for r in retrieved[:top_k]]
    hit_set = set(ranked)
    recalled = [g for g in gold_ids if g in hit_set]
    recall = len(recalled) / max(len(gold_ids), 1) if gold_ids else None
    mrr = None
    if gold_ids:
        for rank, cid in enumerate(ranked, start=1):
            if cid in gold_ids:
                mrr = round(1.0 / rank, 4)
                break
        if mrr is None:
            mrr = 0.0
    return {
        "gold_chunk_count": len(gold_ids),
        "gold_chunk_ids": gold_ids,
        "gold_evidence_hit": bool(recalled),
        "recall_at_k": None if recall is None else round(recall, 4),
        "mrr_at_k": mrr,
        "retrieved_count": len(ranked),
        "empty_retrieval": len(ranked) == 0,
        "comparable": False,
        "comparable_reason": "offline_placeholder 使用非语义 stub 向量，指标仅验证链路",
    }


def generation_metrics(answer: str, expected_keywords: list[str], expected_behavior: str) -> dict:
    text = answer or ""
    keywords = [k for k in (expected_keywords or []) if k]
    hits = sum(1 for k in keywords if _norm(k) in _norm(text))
    abstained = "现有资料无法回答" in text
    return {
        "answer_non_empty": bool(text.strip()),
        "answer_char_len": len(text),
        "keyword_hit_rate": round(hits / len(keywords), 4) if keywords else None,
        "abstained": abstained,
        "abstain_correctness": (abstained == (expected_behavior in ("abstain_no_evidence", "handle_empty_document"))),
        "has_citation_marker": bool(CITATION.search(text)),
        "comparable": False,
    }


def step_latency_summary(records: list[dict]) -> dict:
    acc: dict[str, list[int]] = {}
    for rec in records:
        for step in rec.get("chain", []):
            acc.setdefault(step["step"], []).append(int(step.get("duration_ms") or 0))
    return {
        name: {"count": len(vals), "avg_ms": round(sum(vals) / len(vals), 2), "max_ms": max(vals)}
        for name, vals in sorted(acc.items())
    }
