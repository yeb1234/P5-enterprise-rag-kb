#!/usr/bin/env python3
"""全量公开集基线跑批：分批 + 指数退避(带抖动) + 断点续跑 + 进度快照。

为什么需要它
------------
全量 SQuAD validation 有 11,873 条，单次跑完要几小时，中途必然遇到
TPM 限流和偶发网络抖动。本脚本把长跑拆成可恢复的批次：
- 每批 BATCH 条，用 run_eval.py --offset/--limit 精确取，命令行不会超长；
- 每批失败项靠"runner 只对 ok 写缓存"自动补齐，指数退避等待；
- 进度写 progress.json（已完成 offset + 累计 run_id），Ctrl+C 后重跑同一命令续上；
- 每批 summary 落盘，最后汇总成全量基线报告。

不删任何历史 run；所有批次 run 都保留，可追溯。

用法：
    python evalbench/run_full_baseline.py --label squad-full-A \
      --dataset public/squad_v2_full.jsonl --batch 400 \
      --models deepseek-v4.1-flash-1017 --prompts v4_en_grounded --params A
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EB = ROOT / "evalbench"
PROGRESS = EB / "out" / "full_baseline_progress.json"


def load_progress(label: str) -> dict:
    if PROGRESS.exists():
        data = json.loads(PROGRESS.read_text(encoding="utf-8"))
        if data.get("label") == label:
            return data
    return {"label": label, "done_offsets": [], "batch_runs": {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def save_progress(p: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_summary(after_mtime: float) -> Path | None:
    """找 after_mtime 之后生成的 run summary，避免靠排序猜错。"""
    cands = [p for p in (EB / "runs").glob("RUN-*/summary.json")
             if p.stat().st_mtime >= after_mtime - 2]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def wait_with_jitter(base: float, factor: float, cap: float, attempt: int) -> float:
    raw = min(base * (factor ** attempt), cap)
    jittered = raw * random.uniform(0.8, 1.2)  # ±20% 抖动，避免多进程同频重试
    print(f"  指数退避: base={base:.0f}s × {factor}^{attempt} = {raw:.0f}s, "
          f"抖动后 sleep {jittered:.0f}s", flush=True)
    time.sleep(jittered)
    return jittered


def main() -> int:
    ap = argparse.ArgumentParser(description="全量基线：分批 + 指数退避 + 断点续跑")
    ap.add_argument("--label", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--batch", type=int, default=400)
    ap.add_argument("--total", type=int, default=0, help="样本总数，0=自动从文件读")
    ap.add_argument("--models", default="deepseek-v4.1-flash-1017")
    ap.add_argument("--prompts", default="v4_en_grounded")
    ap.add_argument("--params", default="A")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--factor", type=float, default=1.6)
    ap.add_argument("--cap", type=float, default=300.0)
    ap.add_argument("--max-retries", type=int, default=12, help="每批最多重试轮数")
    ap.add_argument("--offline", action="store_true", help="离线冒烟用，不调 API")
    args = ap.parse_args()

    dataset_path = EB / "datasets" / args.dataset
    total = args.total or sum(1 for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip())
    progress = load_progress(args.label)
    print(f"全量基线 {args.label}: 总计 {total} 条, 批大小 {args.batch}, "
          f"已完成 {len(progress['done_offsets'])} 批", flush=True)

    for offset in range(0, total, args.batch):
        if offset in progress["done_offsets"]:
            print(f"[SKIP] offset={offset} 已完成", flush=True)
            continue
        limit = min(args.batch, total - offset)
        print(f"\n===== 批次 offset={offset} limit={limit} =====", flush=True)

        batch_ok = False
        for retry in range(args.max_retries):
            before = time.time()
            cmd = [sys.executable, str(EB / "run_eval.py"),
                   "--label", f"{args.label}-o{offset}-r{retry}",
                   "--dataset", args.dataset, "--offset", str(offset), "--limit", str(limit),
                   "--models", args.models, "--prompts", args.prompts, "--params", args.params,
                   "--quiet"] + (["--offline"] if args.offline else [])
            proc = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8")
            if proc.returncode != 0:
                print(f"  run_eval 返回 {proc.returncode}", flush=True)

            summ_path = latest_summary(before)
            if summ_path is None:
                print("  未找到本批 summary", flush=True)
                wait_with_jitter(args.wait, args.factor, args.cap, retry)
                continue
            summ = json.loads(summ_path.read_text(encoding="utf-8"))
            counts = summ.get("status_counts", {})
            failed = sum(v for k, v in counts.items() if k != "ok")
            print(f"  run_id={summ['run_id']} status={counts} 失败={failed}", flush=True)
            if failed == 0:
                progress["done_offsets"].append(offset)
                progress["batch_runs"][str(offset)] = {
                    "run_id": summ["run_id"], "n_ok": counts.get("ok", 0),
                    "summary": summ,
                }
                save_progress(progress)
                batch_ok = True
                break
            wait_with_jitter(args.wait, args.factor, args.cap, retry)

        if not batch_ok:
            print(f"[FATAL] offset={offset} 重试 {args.max_retries} 轮仍有失败，停止。"
                  f"进度已保存，可稍后重跑续上。", file=sys.stderr, flush=True)
            return 1

    # 全量完成：汇总各批
    total_ok = sum(v["n_ok"] for v in progress["batch_runs"].values())
    out = EB / "out" / f"{args.label}_full_summary.json"
    out.write_text(json.dumps({
        "label": args.label, "dataset": args.dataset, "total": total,
        "total_ok": total_ok, "batches": len(progress["batch_runs"]),
        "model": args.models, "prompt": args.prompts, "params": args.params,
        "batch_runs": progress["batch_runs"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n全量完成: {total_ok}/{total} 条, {len(progress['batch_runs'])} 批, 汇总 {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
