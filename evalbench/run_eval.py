#!/usr/bin/env python3
"""评估主入口。

示例：
  python evalbench/run_eval.py --offline --label smoke --models minimax-m3 --prompts v1_baseline --params A
  python evalbench/run_eval.py --label v1-baseline --models minimax-m3,kimi-k2.6,deepseek-v4.1-flash-1017 \
      --prompts v1_baseline --params A
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from evalbench.core import runner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="evalbench 真实 API 评估流程")
    ap.add_argument("--label", required=True, help="本轮标签，写入 config_snapshot")
    ap.add_argument("--models", default="minimax-m3", help="逗号分隔的模型 id")
    ap.add_argument("--prompts", default="v1_baseline", help="逗号分隔的提示词版本")
    ap.add_argument("--params", default="A", help="逗号分隔的参数组 id（A/B/C）")
    ap.add_argument("--samples", default="", help="逗号分隔的样本 id；留空为全部")
    ap.add_argument("--dataset", default="samples_v1.jsonl",
                    help="样本文件，相对 evalbench/datasets/；公开集用 public/xxx.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="只取 N 条样本（冒烟/分批用）")
    ap.add_argument("--offset", type=int, default=0, help="从第 N 条样本开始（分批跑用，配合 --limit）")
    ap.add_argument("--repeats", type=int, default=1, help="每组合重复次数")
    ap.add_argument("--offline", action="store_true", help="离线占位，comparable=false，不调用 API")
    ap.add_argument("--list-samples", action="store_true", help="列出样本后退出")
    ap.add_argument("--quiet", action="store_true", help="不打印逐条进度")
    args = ap.parse_args()

    if args.list_samples:
        for s in runner.load_samples(args.dataset):
            print(f"{s['id']}\t{s['task_category']:<24}\t{s['expected_behavior']:<8}\t{s['question']}")
        return 0

    sample_ids = [x.strip() for x in args.samples.split(",") if x.strip()] or None
    if args.limit or args.offset:
        all_ids = [s["id"] for s in runner.load_samples(args.dataset)]
        end = args.offset + args.limit if args.limit else None
        sample_ids = all_ids[args.offset:end]
        if args.samples:
            sample_ids = [x for x in sample_ids if x in args.samples.split(",")]

    run_id, summary = runner.run_stage(
        label=args.label,
        models=[x.strip() for x in args.models.split(",") if x.strip()],
        prompts=[x.strip() for x in args.prompts.split(",") if x.strip()],
        params=[x.strip() for x in args.params.split(",") if x.strip()],
        sample_ids=sample_ids,
        offline=args.offline,
        repeats=args.repeats,
        verbose=not args.quiet,
        dataset_file=args.dataset,
    )
    print(json.dumps({
        "run_id": run_id,
        "mode": summary["mode"],
        "comparable": summary["comparable"],
        "git": summary["git"],
        "n_records": summary["n_records"],
        "status_counts": summary["status_counts"],
        "by_model": summary["by_model"],
        "run_dir": f"evalbench/runs/{run_id}",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
