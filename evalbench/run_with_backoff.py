#!/usr/bin/env python3
"""带限流退避等待的评估入口：失败就等一会儿重跑，直到补齐。

为什么单独写而不改 runner
--------------------------
runner.call_live() 已对 429 做了退避重试，但网关的 `Cluster TPM rate limit exceeded`
是分钟级窗口，短退避不够；且 runner 只对 status=ok 写缓存，所以**重跑同一命令
会自动跳过已成功的样本、只补失败的**。本脚本把这个"重跑补齐"自动化：
  1. 调 run_eval.py 跑一轮；
  2. 读该轮 summary.json 的 status_counts，统计非 ok 数；
  3. 若还有失败 → 指数退避 sleep（默认 30s 起，×1.5，封顶 120s）后重跑；
  4. 直到全 ok 或达到 --max-rounds。

每轮都产生独立 run_id（不覆盖历史），失败的 run 也保留——负结果可查。
run_eval.py 的参数原样透传，用 `--` 分隔。

用法：
    python evalbench/run_with_backoff.py --label public-cmrc-v3 \
      -- python evalbench/run_eval.py --dataset public/cmrc2018_sample_60.jsonl \
         --models deepseek-v4.1-flash-1017 --prompts v3_structured --params B --quiet
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def last_run_dir() -> Path | None:
    runs = sorted((ROOT / "evalbench" / "runs").glob("RUN-*"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[0] if runs else None


def main() -> int:
    ap = argparse.ArgumentParser(description="限流自动等待重跑，直到评估补齐")
    ap.add_argument("--label", required=True, help="本轮标签（写入每轮 config_snapshot）")
    ap.add_argument("--max-rounds", type=int, default=8, help="最多重跑轮数")
    ap.add_argument("--wait", type=float, default=30.0, help="首轮失败后等待秒数")
    ap.add_argument("--factor", type=float, default=1.5, help="每轮等待倍率")
    ap.add_argument("--cap", type=float, default=120.0, help="单次等待上限秒数")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="实际评估命令（前面加 -- 分隔）")
    args = ap.parse_args()

    cmd = [x for x in args.cmd if x != "--"]
    if not cmd:
        print("错误：-- 后需提供评估命令", file=sys.stderr)
        return 2

    wait = args.wait
    all_run_ids: list[str] = []
    for rnd in range(1, args.max_rounds + 1):
        print(f"\n===== 第 {rnd}/{args.max_rounds} 轮（等待 {wait:.0f}s 策略） =====", flush=True)
        # 每轮换 label，避免 run_id 碰撞（run_id 含 nonce 本就不撞，但 label 便于追溯）
        full_cmd = cmd + (["--label", f"{args.label}-r{rnd}"] if "--label" not in cmd else [])
        proc = subprocess.run(full_cmd, cwd=str(ROOT), text=True, encoding="utf-8")
        if proc.returncode != 0:
            print(f"评估命令返回 {proc.returncode}", file=sys.stderr)

        run_dir = last_run_dir()
        if run_dir is None or not (run_dir / "summary.json").exists():
            print("未找到本轮 summary.json，等待后重试", flush=True)
            time.sleep(wait)
            wait = min(wait * args.factor, args.cap)
            continue

        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        run_id = summary.get("run_id", run_dir.name)
        all_run_ids.append(run_id)
        counts = summary.get("status_counts", {})
        failed = sum(v for k, v in counts.items() if k != "ok")
        print(f"run_id={run_id} status={counts} 失败={failed}", flush=True)

        if failed == 0:
            print(f"\n全部补齐，共 {rnd} 轮。run_ids: {all_run_ids}", flush=True)
            out = ROOT / "evalbench" / "out" / f"{args.label}_backoff.json"
            out.write_text(json.dumps({
                "label": args.label, "rounds": rnd, "all_run_ids": all_run_ids,
                "final_run_id": run_id, "final_status_counts": counts,
                "final_summary": summary,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"落盘: {out}", flush=True)
            return 0

        if rnd < args.max_rounds:
            print(f"还有 {failed} 条失败，等待 {wait:.0f}s 后重跑（已成功的走缓存）...", flush=True)
            time.sleep(wait)
            wait = min(wait * args.factor, args.cap)

    print(f"达到最大轮数 {args.max_rounds}，仍有失败。run_ids: {all_run_ids}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
