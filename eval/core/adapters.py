"""占位适配器：所有外部能力在离线档位下的确定性替身。

红线：stub 只验证「链路连通、字段契约、记录格式、故障处理」，
不具备语义能力，产出的一切质量指标都带 comparable=false。
内部占位错误分类见 OBJ-ADAPTER-CONTRACTS，不是任何供应商错误码。
"""
from __future__ import annotations

import hashlib
import math
import re

CJK = re.compile(r"[\u4e00-\u9fff]")
TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")
SENSITIVE = re.compile(r"1[3-9]\d{9}|\d{17}[\dXx]")
CITATION = re.compile(r"\[\d+\]")


class PlaceholderUpstreamTimeout(RuntimeError):
    code = "E_PLACEHOLDER_TIMEOUT"


class PlaceholderMalformedResponse(RuntimeError):
    code = "E_PLACEHOLDER_MALFORMED_RESPONSE"


class ScoringUnavailable(RuntimeError):
    code = "E_PLACEHOLDER_SCORING_UNAVAILABLE"


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", (text or "").lower()).strip()
    words = TOKEN.findall(cleaned)
    bigrams = [words[i] + words[i + 1] for i in range(len(words) - 1)]
    return words + bigrams


def stub_embed(texts: list[str], dim: int = 64) -> list[list[float]]:
    out = []
    for text in texts:
        vec = [0.0] * dim
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % dim
            vec[idx] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([round(v / norm, 8) for v in vec])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def lexical_score(query: str, content: str) -> float:
    q = set(_tokens(query))
    c = set(_tokens(content))
    return len(q & c) / max(len(q), 1)


def extractive_answer(query: str, context_items: list[dict], cfg: dict, fault: str | None = None) -> dict:
    """占位生成：只复述召回片段并标注 [n]，不编造事实。"""
    if fault == "upstream_timeout":
        raise PlaceholderUpstreamTimeout("内部占位：生成节点超时")
    if fault == "upstream_abnormal_response":
        raise PlaceholderMalformedResponse("内部占位：返回结构不符合占位契约")

    abstain = cfg["offline_placeholder_values"]["max_answer_chars"]
    if not context_items:
        return {
            "answer_text": "现有资料无法回答：未检索到可依据的片段。",
            "abstained": True, "cited_indices": [],
            "is_placeholder": True, "model_id": "__TBC__",
        }
    lines = [f"根据资料片段，针对「{query}」可得出以下结论："]
    for i, item in enumerate(context_items[:8], start=1):
        snippet = item["snippet"]
        lines.append(f"· {snippet} [{i}]")
    text = "\n".join(lines)
    return {
        "answer_text": text[:abstain], "abstained": False,
        "cited_indices": list(range(1, min(len(context_items), 8) + 1)),
        "is_placeholder": True, "model_id": "__TBC__",
    }


def rule_score(payload: dict, fault: str | None = None) -> dict:
    """占位评分：只跑规则检查，不产出可比较的综合分。"""
    if fault == "scoring_unavailable":
        return {
            "status": "unavailable", "reason": "内部占位：评分器不可用",
            "checks": {}, "composite_score": None, "comparable": False,
        }
    answer = payload.get("answer_text") or ""
    context_items = payload.get("context_items") or []
    expected = payload.get("expected_behavior")
    context_text = " ".join(i["snippet"] for i in context_items)
    ans_tokens, ctx_tokens = set(_tokens(answer)), set(_tokens(context_text))
    grounded = len(ans_tokens & ctx_tokens) / max(len(ans_tokens), 1)
    abstained = "现有资料无法回答" in answer
    want_abstain = expected in ("abstain_no_evidence", "handle_empty_document")
    checks = {
        "R1_answer_non_empty": bool(answer.strip()),
        "R2_citation_format_valid": bool(CITATION.search(answer)) if context_items and not abstained else True,
        "R3_answer_grounded_in_context": round(min(grounded, 1.0), 4),
        "R4_abstain_when_no_evidence": (abstained == want_abstain) if (want_abstain or not context_items) else None,
        "R5_no_sensitive_pattern_leak": not bool(SENSITIVE.search(answer)),
        "R6_prompt_overflow_recorded": bool(payload.get("prompt_overflow_recorded") is not None),
    }
    return {
        "status": "ok", "checks": checks, "composite_score": None,
        "comparable": False, "composite_freeze_status": "TBC__not_frozen",
    }
