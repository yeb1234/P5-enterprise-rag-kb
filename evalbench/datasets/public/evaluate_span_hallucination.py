#!/usr/bin/env python3
"""RAGTruth span 级标注复现（确定性规则，不靠 LLM 裁判）。

复现对象
--------
RAGTruth 论文的人工 span 标注：每个幻觉片段标 label_type
（Evident Conflict / Evident Baseless Info / Subtle Baseless Info）与字符区间 [start,end)。

本脚本不重跑人工标注，而是在**数据集自带的 output + context + 人工 span** 上，
用确定性规则自动检出疑似幻觉 span，再与人工 span 做字符级 IOU 对拍，
报告 span 级 precision / recall / F1。这是"自动复现人工 span 标注aligned程度"的评估，
不是"复现论文人工标注过程本身"——后者需要标注员，无法用脚本替代。

检测规则（针对 Baseless Info 与 Conflict 两类可确定性判定的模式）
-------------------------------------------------------------
1. 把 output 按句切成候选 span（保留字符偏移）。
2. 每句提取"事实锚点"：金额/百分比/数字量、大写专名（地名/机构/产品）。
3. 若某句的锚点**全部**能在 context 找到 → 判忠实，不产出 span。
4. 若某句含 context 中完全没有的锚点 → 产出一个 span（整句），类型 Baseless Info。
5. 若某句锚点在 context 出现但**数值冲突**（同一实体对应不同数字）→ Conflict。
   （本版先实现 Baseless；Conflict 需实体-数值对齐，留作 V2，见三态）

IOU 对拍：自动 span 与人工 span 字符区间 IOU >= 0.5 记命中。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent

# 数字量：$23.70 / 49,400 / 15% / 3 years / 2020
_NUM_RE = re.compile(r"(?:\$|£|€)?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|years?|months?|hours?|million|billion)?", re.I)
# 专名：多词大写短语 或 单个大写词。单词需排除句首位置与常见句首虚词，
# 否则 Then/Fold/Next 这类祈使句首词会被当专名 → 大量误报（实测踩过）。
_PROPER_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
_SENT_STARTERS = {
    "then", "next", "first", "finally", "also", "additionally", "however", "therefore",
    "based", "according", "the", "this", "that", "these", "those", "it", "its", "in",
    "on", "at", "for", "with", "from", "by", "as", "if", "when", "while", "after",
    "before", "fold", "cut", "mix", "add", "place", "take", "make", "use", "keep",
    "step", "note", "example", "overall", "thus", "moreover", "furthermore", "instead",
}
# 分句：句号/问号/感叹号后跟空格或结尾，但小数点（数字.数字）不当句末。
# 实测 "$23.70" 会被旧正则切成 "23." + "70"，裁判拿到残句。
_SENT_RE = re.compile(r"[^.!?]+(?<!\d)[.!?](?=\s|$)|[^.!?]+$")


def anchors(text: str) -> set[str]:
    out = set()
    for m in _NUM_RE.finditer(text):
        out.add(re.sub(r"\s+", " ", m.group(0).lower()).strip())
    for m in _PROPER_RE.finditer(text):
        w = m.group(0)
        # 单个大写词且位于句首 → 大概率是句首虚词/动词，不是专名
        single = " " not in w.strip()
        if single and m.start() <= 1 and w.lower() in _SENT_STARTERS:
            continue
        if single and w.lower() in _SENT_STARTERS:
            continue
        out.add(w.lower())
    return out


def detect_spans(output: str, context: str) -> list[dict]:
    ctx_anchors = anchors(context)
    ctx_blob = context.lower()
    spans = []
    for m in _SENT_RE.finditer(output):
        sent = m.group(0)
        # 过短片段（列表编号 "3."、孤立数字/缩写）不是事实陈述，跳过。
        # 实测：不过滤会把编号当幻觉 span，precision 掉到 0.036。
        if len(sent.strip()) < 20:
            continue
        a = anchors(sent)
        if not a:
            continue
        # 锚点没有任何一个在 context 出现 → Baseless span
        # 用宽松匹配：数字原文 或 专名子串 出现在 context
        grounded = [x for x in a if x in ctx_blob]
        # 数字做归一化后再比一次（23.7 vs 23.70）
        if not grounded:
            for x in a:
                if any(ch.isdigit() for ch in x):
                    core = re.sub(r"[^\d.]", "", x).rstrip(".")
                    if core and core in re.sub(r"[^\d.]", "", ctx_blob):
                        grounded.append(x)
        # 过半锚点无依据才判 hallucination：单句常有 1~2 个(context 里没有的)过渡性专名，
        # 全有/全无的二值判据实测误报过高。
        if len(grounded) <= len(a) / 2:
            spans.append({"start": m.start(), "end": m.end(),
                          "text": sent.strip(), "label_type": "Evident Baseless Info"})
    return spans


def iou(a: dict, b: dict) -> float:
    s = max(a["start"], b["start"])
    e = min(a["end"], b["end"])
    inter = max(0, e - s)
    union = (a["end"] - a["start"]) + (b["end"] - b["start"]) - inter
    return inter / union if union > 0 else 0.0


def match_spans(pred: list[dict], gold: list[dict], thr: float = 0.5) -> tuple[int, int, int]:
    used = set()
    hits = 0
    for p in pred:
        best, bi = 0.0, -1
        for i, g in enumerate(gold):
            if i in used:
                continue
            v = iou(p, g)
            if v > best:
                best, bi = v, i
        if best >= thr:
            hits += 1
            used.add(bi)
    return hits, len(pred), len(gold)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="QA", choices=("QA", "Summary", "Data2txt", "ALL"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--iou-threshold", type=float, default=0.5)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = pq.read_table(HERE / "raw" / "ragtruth_processed_test.parquet").to_pylist()
    if args.split != "ALL":
        rows = [r for r in rows if r["task_type"] == args.split]
    if args.limit:
        rows = rows[:args.limit]

    records = []
    for r in rows:
        gold = json.loads(r["hallucination_labels"]) if r["hallucination_labels"] else []
        pred = detect_spans(r["output"], r["context"])
        h, np_, ng = match_spans(pred, gold, args.iou_threshold)
        records.append({
            "id": r["id"], "task_type": r["task_type"],
            "n_gold": ng, "n_pred": np_, "n_hit": h,
            "gold_labels": [s.get("label_type") for s in gold],
            "pred_spans": [{"start": s["start"], "end": s["end"], "text": s["text"][:120]} for s in pred],
        })

    tp = sum(r["n_hit"] for r in records)
    npred = sum(r["n_pred"] for r in records)
    ngold = sum(r["n_gold"] for r in records)
    precision = tp / npred if npred else None
    recall = tp / ngold if ngold else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None

    summary = {
        "split": args.split, "n_samples": len(records), "iou_threshold": args.iou_threshold,
        "gold_spans": ngold, "pred_spans": npred, "hits": tp,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "method": "deterministic anchor-based span detection (NOT paper's human annotation, NOT LLM-as-judge)",
        "caveats": ["只覆盖 Baseless Info 型；Evident Conflict 未实现",
                    "句子级粗粒度，人工 span 可能只是句中短语 → IOU 偏低属预期",
                    "专名识别对缩写/小写实体不鲁棒"],
    }
    out = Path(args.out) if args.out else HERE / f"span_eval_{args.split.lower()}.json"
    out.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("落盘:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
