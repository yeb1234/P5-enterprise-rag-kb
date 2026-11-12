#!/usr/bin/env python3
"""前后对照：同轮内按提示词/参数对比，或跨轮按同一模型与类别对比。

用法：
  python evalbench/compare.py --a RUN-xxx --b RUN-yyy --label v1_vs_v2
  python evalbench/compare.py --within RUN-xxx --by prompt_version --label prompt_compare
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIMS = ("answer_accuracy", "rejection_correctness", "faithfulness", "format_compliance")


def _load(run_id: str) -> dict:
    return json.loads((ROOT / "runs" / run_id / "summary.json").read_text(encoding="utf-8"))


def _index(summary: dict, *, by: str | None = None) -> dict:
    idx = defaultdict(list)
    for g in summary["groups"]:
        key = (g["model"], g["task_category"], g["dimension"],
               g[by] if by else "all")
        idx[key].append(g["mean"])
    return {k: sum(v) / len(v) for k, v in idx.items()}


def compare_across(a_id: str, b_id: str) -> dict:
    a, b = _load(a_id), _load(b_id)
    ia, ib = _index(a), _index(b)
    keys = sorted(set(ia) | set(ib))
    rows = []
    for model, cat, dim, _ in keys:
        va, vb = ia.get((model, cat, dim, "all")), ib.get((model, cat, dim, "all"))
        rows.append({
            "model": model, "task_category": cat, "dimension": dim,
            "baseline": va, "candidate": vb,
            "delta": round(vb - va, 4) if (va is not None and vb is not None) else None,
        })
    primary_dims = [r for r in rows if r["dimension"] in ("answer_accuracy", "rejection_correctness")]
    deltas = [r["delta"] for r in primary_dims if r["delta"] is not None]
    return {
        "kind": "across_runs",
        "baseline_run": a_id, "baseline_label": a.get("label"), "baseline_git": a.get("git"),
        "candidate_run": b_id, "candidate_label": b.get("label"), "candidate_git": b.get("git"),
        "aligned_on": ["model", "task_category", "dimension"],
        "rows": rows,
        "mean_primary_delta": round(sum(deltas) / len(deltas), 4) if deltas else None,
        "comparable": bool(a.get("comparable") and b.get("comparable")),
        "adoption_rule": "negative_rejection 的 rejection_correctness 不下降，且 answer_accuracy/faithfulness 均不下降、至少一项提升",
    }


def compare_within(run_id: str, by: str) -> dict:
    s = _load(run_id)
    idx = _index(s, by=by)
    variants = sorted({k[3] for k in idx})
    rows = []
    for model, cat, dim in sorted({(k[0], k[1], k[2]) for k in idx}):
        vals = {v: idx.get((model, cat, dim, v)) for v in variants}
        rows.append({"model": model, "task_category": cat, "dimension": dim,
                     "variants": {k: v for k, v in vals.items() if v is not None}})
    return {
        "kind": "within_run", "run": run_id, "label": s.get("label"),
        "git": s.get("git"), "compared_by": by, "variants": variants,
        "rows": rows, "comparable": bool(s.get("comparable")),
        "by_model_overall": s.get("by_model"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", help="基线 run_id")
    ap.add_argument("--b", help="候选 run_id")
    ap.add_argument("--within", help="单个 run_id 做组内对比")
    ap.add_argument("--by", default="prompt_version", choices=["prompt_version", "params_id"])
    ap.add_argument("--label", required=True, help="输出文件名（不含扩展名）")
    args = ap.parse_args()

    result = compare_within(args.within, args.by) if args.within else compare_across(args.a, args.b)
    out = ROOT / "iterations" / "comparisons" / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "written": str(out.relative_to(ROOT.parent)),
        "kind": result["kind"], "comparable": result["comparable"],
        "rows": len(result["rows"]),
        "mean_primary_delta": result.get("mean_primary_delta"),
        "variants": result.get("variants"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
