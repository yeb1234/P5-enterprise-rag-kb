#!/usr/bin/env python3
"""RAGTruth 忠实度专项评估（机械幻觉检测，不依赖第二个 LLM 当裁判）。

设计边界（先读，避免误读结果）：
- RAGTruth 的 `score` 是「数据集自带 response 是否有幻觉」的标注，**不是我们系统答案的金标**。
  所以本脚本不拿 score 直接给我们打分，而是用它把样本分成两组：
    score=0 → 原 response 有幻觉 → 视为「高幻觉风险」题干（证据更容易诱发编造）
    score=1 → 原 response 忠实   → 对照组
- 我们系统对同一 question + passages 生成答案，用**确定性规则**检测幻觉：
    答案中的数字量（金额/百分比/年数）与专有名词（大写开头的连续词）若不出现在 passages，
    即记为「证据外实体」疑似幻觉。
- 这不是 RAGTruth 论文的 span 级人工标注，也不复用它的 response；是同考察点（答案是否超出证据）
  的机械近似。判定对同义改写不鲁棒，只报告"疑似率"，不宣称"幻觉率真值"。

用法：
    python evalbench/datasets/public/evaluate_faithfulness.py --limit 20 --model deepseek-v4.1-flash-1017
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # evalbench/
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from evalbench.core import runner  # noqa: E402

# 数字量：$23.70 / 49,400 / 15% / 3 years / 2020
_NUM_RE = re.compile(r"(?:\$|£|€)?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|years?|months?|hours?|million|billion)?", re.I)
# 专有名词：连续大写开头词（跳过句首普通词用 min 2 词或含数字）
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|[A-Z][A-Za-z]*\d+)\b")
_PASSAGE_RE = re.compile(r"passage \d+:")


def split_passages(text: str) -> list[dict]:
    parts = _PASSAGE_RE.split(text)
    out = []
    for i, chunk in enumerate(parts):
        chunk = chunk.strip()
        if not chunk:
            continue
        out.append({"doc_id": f"P{i}", "kind": "positive", "text": chunk})
    return out or [{"doc_id": "P1", "kind": "positive", "text": text.strip()}]


def evidence_entities(passages: list[dict]) -> set[str]:
    blob = " ".join(p["text"].lower() for p in passages)
    ents = set()
    for m in list(_NUM_RE.finditer(blob)) + list(_PROPER_RE.finditer(blob)):
        ents.add(re.sub(r"\s+", " ", m.group(0).lower()).strip())
    return ents


def suspicious_entities(answer: str, evidence: set[str]) -> list[str]:
    found = []
    for m in list(_NUM_RE.finditer(answer)) + list(_PROPER_RE.finditer(answer)):
        e = re.sub(r"\s+", " ", m.group(0).lower()).strip()
        if not e or e in evidence:
            continue
        # 引用标记不是实体：模型按提示词输出 (P1, P3)，而原 passages 写作 "passage 1:"，
        # 不排除的话 P1/P3 会被当成"证据外专名"，把忠实答案全判成幻觉（实测踩过）。
        if re.fullmatch(r"p\d+", e):
            continue
        # 纯数字/标点碎片无语义（如 "2,"），不构成幻觉证据
        if not any(c.isalpha() for c in e):
            continue
        # 数字若被 evidence 中任一数字包含（如 23.7 vs 23.70），视为匹配，避免误报
        if any(e in ev or ev in e for ev in evidence if any(ch.isdigit() for ch in ev)):
            continue
        found.append(e)
    return sorted(set(found))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="每组抽取条数")
    ap.add_argument("--model", default="deepseek-v4.1-flash-1017")
    ap.add_argument("--prompt", default="v4_en_grounded")
    ap.add_argument("--sleep", type=float, default=0.3, help="每次调用后休眠秒数，避免限流")
    args = ap.parse_args()

    rows = pq.read_table(HERE / "raw" / "ragtruth_qa.parquet").to_pylist()
    groups = {"hallucinated(score0)": [r for r in rows if r["score"] == 0],
              "faithful(score1)": [r for r in rows if r["score"] == 1]}
    template, _ = runner.load_prompt(args.prompt)
    params = runner.load_params("B")
    model_cfg = {m["id"]: m for m in runner.load_models()["selected"]}[args.model]

    out_path = HERE / f"faithfulness_{args.limit}per.jsonl"
    results = []
    for gname, grows in groups.items():
        for r in grows[:args.limit]:
            info = json.loads(r["source_info"])
            passages = split_passages(info["passages"])
            prompt = runner.render_prompt(template, {"question": info["question"]}, passages)
            call = runner.call_live(model_cfg, prompt, params)
            ev = evidence_entities(passages)
            susp = suspicious_entities(call.get("answer", ""), ev) if call["status"] == "ok" else []
            results.append({
                "group": gname, "ragtruth_id": r["id"], "score": r["score"],
                "question": info["question"], "status": call["status"],
                "answer": call.get("answer", ""), "suspicious_entities": susp,
                "has_suspicion": bool(susp), "latency_ms": call.get("latency_ms", 0),
            })
            print(f"[{gname:<20}] id={r['id']:<6} status={call['status']:<12} "
                  f"suspicion={len(susp)} {susp[:3]}", flush=True)
            time.sleep(args.sleep)

    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {}
    for gname in groups:
        rs = [r for r in results if r["group"] == gname]
        ok = [r for r in rs if r["status"] == "ok"]
        summary[gname] = {
            "n": len(rs), "n_ok": len(ok),
            "suspicion_rate": round(sum(r["has_suspicion"] for r in ok) / len(ok), 4) if ok else None,
            "avg_suspicious_entities": round(sum(len(r["suspicious_entities"]) for r in ok) / len(ok), 3) if ok else None,
            "avg_latency_ms": round(sum(r["latency_ms"] for r in ok) / len(ok), 1) if ok else None,
        }
    print("\n汇总:", json.dumps(summary, ensure_ascii=False, indent=2))
    (HERE / f"faithfulness_{args.limit}per_summary.json").write_text(
        json.dumps({"config": vars(args), "summary": summary,
                    "method": "mechanical entity/number check against passages (NOT LLM-as-judge, NOT paper's span labels)"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
