#!/usr/bin/env python3
"""把公开数据集转换成 evalbench 样本格式（分层抽样，固定种子可复现）。

数据源（均从 hf-mirror 国内镜像下载，原始 parquet 存于 raw/）：
- SQuAD v2 validation（rajpurkar/squad_v2，CC BY-SA 4.0）：
  context + question + answers。可答题取多数答案作 key_facts；不可答题标 abstain。
- RAGTruth test / qa（flowaicom/RAGTruth_test，MIT）：
  source_info 里的 question + passages 用于忠实度专项（见 evaluate_faithfulness.py）。
  **它的 response/score 是对既有回答的幻觉标注，不是金标答案，本脚本不把它当 key_facts。**

抽样：固定 seed 分层抽取，可答/不可答各 N 条，避免 11873 条全量跑爆 API 配额。
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"


def build_squad(n_per_class: int, seed: int) -> list[dict]:
    rows = pq.read_table(RAW / "squad_v2_validation.parquet").to_pylist()
    answerable = [r for r in rows if r["answers"]["text"]]
    unanswerable = [r for r in rows if not r["answers"]["text"]]
    rng = random.Random(seed)
    rng.shuffle(answerable)
    rng.shuffle(unanswerable)

    # 交错两类，避免 --limit N 只取到一整类（小样探路时踩过：前 6 条全是可答题）。
    groups = [(answerable[:n_per_class], "grounded_extraction"),
              (unanswerable[:n_per_class], "negative_rejection")]
    pairs = [(groups[0][0][i], groups[0][1]) for i in range(n_per_class)] + \
            [(groups[1][0][i], groups[1][1]) for i in range(n_per_class)]
    rng.shuffle(pairs)

    out: list[dict] = []
    for r, tag in pairs:
        is_abstain = tag == "negative_rejection"
        # 多数投票选金标（SQuAD 多个标注者可能给不同 span）
        votes = Counter(x.strip() for x in r["answers"]["text"] if x.strip())
        gold = votes.most_common(1)[0][0] if votes else ""
        rec = {
            "id": "SQ_" + r["id"],
            "task_category": tag,
            "dimensions": ["answer_accuracy", "rejection_correctness", "faithfulness",
                           "format_compliance", "answer_economy"],
            "question": r["question"].strip(),
            "passages": [{"doc_id": "S1", "kind": "positive", "text": r["context"].strip()}],
            "reference_answer": gold,
            "key_facts": [] if is_abstain else ([gold] if gold else []),
            "forbidden_facts": [],
            "expected_behavior": "abstain" if is_abstain else "answer",
            "source_basis": "SQuAD v2 validation (rajpurkar/squad_v2, CC BY-SA 4.0)",
            "note": "公开集；" + ("不可回答，段落相关但无答案" if is_abstain else "可回答，答案为标注 span"),
        }
        out.append(rec)
    return out


def build_cmrc(n: int, seed: int) -> list[dict]:
    """CMRC 2018 全部是可答题，结构同 SQuAD；只测抽取准确率，不测拒答。"""
    rows = pq.read_table(RAW / "cmrc2018_validation.parquet").to_pylist()
    rng = random.Random(seed)
    rng.shuffle(rows)
    out: list[dict] = []
    for r in rows[:n]:
        votes = Counter(x.strip() for x in r["answers"]["text"] if x.strip())
        gold = votes.most_common(1)[0][0] if votes else ""
        if not gold:
            continue
        out.append({
            "id": "CMRC_" + r["id"],
            "task_category": "grounded_extraction",
            "dimensions": ["answer_accuracy", "rejection_correctness", "faithfulness",
                           "format_compliance", "answer_economy"],
            "question": r["question"].strip(),
            "passages": [{"doc_id": "C1", "kind": "positive", "text": r["context"].strip()}],
            "reference_answer": gold,
            "key_facts": [gold],
            "forbidden_facts": [],
            "expected_behavior": "answer",
            "source_basis": "CMRC 2018 validation (hfl/cmrc2018, CC BY-SA 4.0)",
            "note": "中文公开集；抽取式 QA，全部可答，答案为标注 span",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-class", type=int, default=30, help="SQuAD 可答/不可答各取多少条")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dataset", choices=("squad", "cmrc"), default="squad",
                    help="适配哪个公开集")
    ap.add_argument("--cmrc-n", type=int, default=60, help="CMRC 抽取条数")
    args = ap.parse_args()

    if args.dataset == "cmrc":
        samples = build_cmrc(args.cmrc_n, args.seed)
        out_path = HERE / f"cmrc2018_sample_{args.cmrc_n}.jsonl"
    else:
        samples = build_squad(args.n_per_class, args.seed)
        out_path = HERE / f"squad_v2_sample_{args.n_per_class * 2}.jsonl"

    with out_path.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    dist = Counter(s["task_category"] for s in samples)
    print(f"写出 {out_path.name}: {len(samples)} 条, 分层={dict(dist)}, seed={args.seed}")
    print("前 2 条:")
    for s in samples[:2]:
        print(f"  {s['id']} [{s['task_category']}] Q={s['question'][:60]}")
        print(f"    key_facts={s['key_facts']} behavior={s['expected_behavior']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
