"""逐用例记录查看器 —— 演示时回答「这条记录到底长什么样」。

用法：
  python demo/inspect_record.py C-001
  python demo/inspect_record.py C-020 --run RUN-095a2a4a40c6
  python demo/inspect_record.py C-001 --json        # 结构化输出，供自测/对齐使用
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"


def resolve_run(run_id: str | None) -> Path:
    if run_id:
        candidate = RESULTS / run_id
        if not (candidate / "records.jsonl").exists():
            raise SystemExit(f"找不到 run：{candidate}")
        return candidate
    candidates = sorted(
        (p.parent for p in RESULTS.glob("*/records.jsonl")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit("尚无评测结果，请先运行：python eval/run_eval.py")
    return candidates[0]


def load_case(run_dir: Path, case_id: str) -> dict:
    for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["case_id"] == case_id:
            return rec
    raise SystemExit(f"用例 {case_id} 不在 {run_dir.name} 的记录中")


def summarize(rec: dict, run_dir: Path) -> dict:
    steps = {s["step"]: s for s in rec["chain"]}
    chunk = steps.get("chunk", {}).get("metrics", {})
    retrieve = steps.get("retrieve", {}).get("metrics", {})
    prompt = steps.get("assemble_prompt", {}).get("metrics", {})
    gen = steps.get("generate", {}).get("metrics", {})
    final = rec["final"]
    answer = final.get("answer_text") or ""
    return {
        "run_id": run_dir.name,
        "case_id": rec["case_id"],
        "suite": rec["suite"],
        "scenario": rec["scenario"],
        "status": final["status"],
        "expected_status": final["expected_status"],
        "fault_handled_correct": final["fault_handled_correct"],
        "quality_comparable": final["quality_comparable"],
        "chain": [
            {"step": s["step"], "status": s["status"],
             "object_refs_count": len(s["object_refs"]), "warnings": len(s["warnings"]),
             "has_error": bool(s["error"])}
            for s in rec["chain"]
        ],
        "chunk_count": chunk.get("chunk_count"),
        "empty_document": chunk.get("empty_document"),
        "chunk_truncated": chunk.get("truncated"),
        "forced_empty": retrieve.get("forced_empty"),
        "retrieved_count": retrieve.get("retrieved_count"),
        "recall_at_k": final["retrieval_metrics"]["recall_at_k"],
        "mrr_at_k": final["retrieval_metrics"]["mrr_at_k"],
        "prompt_hash": prompt.get("prompt_hash"),
        "prompt_overflow": prompt.get("prompt_overflow"),
        "answer_chars": gen.get("answer_chars"),
        "has_citation": "[1]" in answer or any(f"[{i}]" in answer for i in range(1, 9)),
        "abstained": "现有资料无法回答" in answer,
        "scoring_status": final["scoring"]["status"],
        "composite_score": final["scoring"]["composite_score"],
        "scoring_checks": final["scoring"]["checks"],
        "errors": rec["errors"],
        "warnings": rec["warnings"],
        "config_snapshot_hash": rec["config_snapshot_hash"],
        "code_hash": rec["code_hash"],
        "dataset_hash": rec["dataset_hash"],
        "object_versions_count": len(rec["object_versions"]),
        "answer_preview": answer[:120],
    }


def print_human(s: dict) -> None:
    print(f"用例 {s['case_id']} | 套件={s['suite']} | 场景={s['scenario']} | 状态={s['status']}（预期 {s['expected_status']}）")
    print(f"run_id={s['run_id']}  comparable={s['quality_comparable']}")
    print("六链路：")
    for i, c in enumerate(s["chain"], start=1):
        print(f"  {i}. {c['step']:<18} {c['status']:<8} 对象版本 {c['object_refs_count']} 个 | 告警 {c['warnings']} | 错误 {'是' if c['has_error'] else '否'}")
    print(f"分块={s['chunk_count']}  召回={s['retrieved_count']}  recall_at_k={s['recall_at_k']}  mrr_at_k={s['mrr_at_k']}")
    print(f"prompt_hash={s['prompt_hash']}  溢出={s['prompt_overflow']}")
    print(f"回答：{s['answer_preview']}")
    print(f"评分状态={s['scoring_status']}  composite_score={s['composite_score']}")
    if s["errors"]:
        print("错误：" + "; ".join(f"{e['code']}" for e in s["errors"]))
    print(f"复现要素：config={s['config_snapshot_hash'][:12]}… code={s['code_hash'][:12]}… data={s['dataset_hash'][:12]}…")


def main() -> int:
    ap = argparse.ArgumentParser(description="查看单个用例的六链路记录")
    ap.add_argument("case_id", help="用例编号，例如 C-001")
    ap.add_argument("--run", default=None, help="指定 run_id，默认取最近一次")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = ap.parse_args()
    run_dir = resolve_run(args.run)
    summary = summarize(load_case(run_dir, args.case_id), run_dir)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print_human(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
