"""确定性评分：按任务类别 / 评估维度分别给分，不依赖第二个 LLM 当裁判。"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

THINK_TAGS = ("<mm:think>", "</mm:think>", "<think>", "</think>")
REJECT_MARKERS = ("INSUFFICIENT", "信息不足", "无法回答", "没有找到", "资料不足", "不能回答", "不足以回答")
# 组合式判定：v1 实测发现「资料与问题无关…无法基于上述资料回答」不含任何单一标记，被误判为未拒答。
# 只要「否定词」与「资料指代」共现即认定为拒答，覆盖自然语言的多样化表达。
NEG_WORDS = ("无法", "不能", "不足以", "没有", "未提及", "不涉及", "无关", "缺乏")
SRC_WORDS = ("资料", "文档", "上下文", "上述", "提供的内容", "信息")


def is_rejection(answer: str) -> bool:
    norm = normalize(answer)
    if any(normalize(m) in norm for m in REJECT_MARKERS):
        return True
    return any(w in norm for w in NEG_WORDS) and any(w in norm for w in SRC_WORDS)
SCORE_DIMS = ("answer_accuracy", "rejection_correctness", "faithfulness",
              "format_compliance", "answer_economy")
ECONOMY_MARKERS = ("\n- ", "\n1.", "\n2.", "\n\n", "###")


def normalize(text: str) -> str:
    """SQuAD 归一化口径 + 中文适配：去空白，让「3 秒」与「3秒」等价。"""
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s)
    return re.sub(r"\s+", "", s)


def load_metrics() -> dict:
    return json.loads((ROOT / "configs" / "metrics.json").read_text(encoding="utf-8"))


def score_sample(sample: dict, answer: str, meta: dict | None = None) -> dict:
    meta = meta or {}
    ans = answer or ""
    norm = normalize(ans)
    out: dict = {}

    facts = [f for f in sample.get("key_facts", []) if f]
    if facts:
        hits = sum(1 for f in facts if normalize(f) in norm)
        out["answer_accuracy"] = round(hits / len(facts), 4)

    rejected = is_rejection(ans)
    expected_abstain = sample.get("expected_behavior") == "abstain"
    out["rejection_correctness"] = 1.0 if rejected == expected_abstain else 0.0

    forbidden = [f for f in sample.get("forbidden_facts", []) if f]
    out["faithfulness"] = 0.0 if any(normalize(f) in norm for f in forbidden) else 1.0

    clean = bool(ans.strip()) and len(ans) <= 600 and not any(t in ans for t in THINK_TAGS)
    out["format_compliance"] = 1.0 if clean else 0.0

    concise = (bool(ans.strip()) and len(ans) <= 200
               and not any(t in ans for t in ECONOMY_MARKERS))
    out["answer_economy"] = 1.0 if concise else 0.0

    out["latency_ms"] = meta.get("latency_ms")
    out["rejected"] = rejected
    return out


def aggregate(records: list[dict]) -> dict:
    """分组聚合：模型 × 提示词版本 × 参数组 × 任务类别 × 维度。"""
    buckets: dict[tuple, list[float]] = defaultdict(list)
    latency: dict[tuple, list[int]] = defaultdict(list)
    skipped = 0
    for r in records:
        if r.get("status") != "ok":
            skipped += 1
            continue
        key = (r.get("model", ""), r.get("prompt_version", ""),
               r.get("params_id", ""), r.get("task_category", ""))
        scores = r.get("scores") or {}
        for d in SCORE_DIMS:
            if scores.get(d) is not None:
                buckets[(key, d)].append(float(scores[d]))
        if scores.get("latency_ms") is not None:
            latency[key].append(int(scores["latency_ms"]))

    by_group: list[dict] = []
    for (key, dim), vals in sorted(buckets.items()):
        by_group.append({
            "model": key[0], "prompt_version": key[1], "params_id": key[2],
            "task_category": key[3], "dimension": dim,
            "mean": round(sum(vals) / len(vals), 4), "n": len(vals),
        })

    primary_map = load_metrics()["category_primary_dimension"]
    by_model: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for g in by_group:
        primary = primary_map.get(g["task_category"])
        if g["dimension"] == primary:
            by_model[g["model"]][g["task_category"]].append(g["mean"])
    by_model_out = {}
    for model, cats in by_model.items():
        per_cat = {c: round(sum(v) / len(v), 4) for c, v in cats.items()}
        by_model_out[model] = {
            "per_category": per_cat,
            "overall": round(sum(per_cat.values()) / len(per_cat), 4) if per_cat else None,
            "avg_latency_ms": None,
        }
    # 汇总全部记录的延迟：按类别分组后若逐个赋值，最后一个类别会覆盖前面的（已修）
    by_model_latency: dict[str, list[int]] = defaultdict(list)
    for key, vals in latency.items():
        by_model_latency[key[0]].extend(vals)
    for m, vals in by_model_latency.items():
        if m in by_model_out and vals:
            by_model_out[m]["avg_latency_ms"] = round(sum(vals) / len(vals), 1)

    return {
        "groups": by_group,
        "by_model": by_model_out,
        "scored_records": sum(1 for r in records if r.get("status") == "ok"),
        "skipped_records": skipped,
    }
