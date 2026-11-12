"""对比实验 / 回归比对：按 case_id 配对，输出逐用例差异与守卫检查。

注意：离线占位 run 之间的对比只用于验证「对比工具与记录格式可用」，
任何质量结论都标记 comparable=false；接入真实 API 后本工具口径不变。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.core.ids import stable_hash  # noqa: E402

METRIC_PATHS = {
    "recall_at_k": ("final", "retrieval_metrics", "recall_at_k"),
    "mrr_at_k": ("final", "retrieval_metrics", "mrr_at_k"),
    "retrieved_count": ("final", "retrieval_metrics", "retrieved_count"),
    "keyword_hit_rate": ("final", "generation_metrics", "keyword_hit_rate"),
    "answer_char_len": ("final", "generation_metrics", "answer_char_len"),
    "has_citation_marker": ("final", "generation_metrics", "has_citation_marker"),
    "grounded_ratio": ("final", "scoring", "checks", "R3_answer_grounded_in_context"),
}


def dig(obj, path):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def load(run_dir: Path) -> dict:
    recs = [json.loads(l) for l in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    return {"records": {r["case_id"]: r for r in recs}, "dir": run_dir}


def compare(base_dir: Path, cand_dir: Path) -> dict:
    base, cand = load(base_dir), load(cand_dir)
    base_manifest = json.loads((base_dir / "manifest.json").read_text(encoding="utf-8"))
    cand_manifest = json.loads((cand_dir / "manifest.json").read_text(encoding="utf-8"))
    common = sorted(set(base["records"]) & set(cand["records"]))
    rows = []
    for cid in common:
        b, c = base["records"][cid], cand["records"][cid]
        deltas = {}
        for name, path in METRIC_PATHS.items():
            bv, cv = dig(b, path), dig(c, path)
            delta = None
            if isinstance(bv, (int, float)) and isinstance(cv, (int, float)):
                delta = round(cv - bv, 6)
            elif bv != cv:
                delta = "changed"
            deltas[name] = {"baseline": bv, "candidate": cv, "delta": delta}
        guard = {
            "status_not_worse": (b["final"]["status"] == c["final"]["status"]
                                 or c["final"]["status"] == "ok"),
            "fault_handling_not_worse": (c["final"]["fault_handled_correct"]
                                         or not b["final"]["fault_handled_correct"]),
            "schema_still_valid": len(c["chain"]) == 6,
        }
        rows.append({"case_id": cid, "baseline_status": b["final"]["status"],
                     "candidate_status": c["final"]["status"],
                     "deltas": deltas, "guard": guard,
                     "guard_pass": all(guard.values())})

    changed_objects = [
        k for k in set(base_manifest["object_versions"]) | set(cand_manifest["object_versions"])
        if base_manifest["object_versions"].get(k) != cand_manifest["object_versions"].get(k)
    ]
    comparable = (
        base_manifest["profile"] != "offline_placeholder"
        and cand_manifest["profile"] != "offline_placeholder"
        and not cand_manifest.get("variant_overrides")
    )
    result = {
        "schema": "eval-comparison/v0.1",
        "baseline_run_id": base_manifest["run_id"],
        "candidate_run_id": cand_manifest["run_id"],
        "baseline_config_hash": base_manifest["registry_snapshot"]["config_hash"],
        "candidate_config_hash": cand_manifest["registry_snapshot"]["config_hash"],
        "variant_overrides": cand_manifest.get("variant_overrides", []),
        "changed_object_versions": changed_objects,
        "case_count": len(common),
        "guard_pass_rate": round(sum(1 for r in rows if r["guard_pass"]) / max(len(rows), 1), 4),
        "guard_failures": [r["case_id"] for r in rows if not r["guard_pass"]],
        "cases": rows,
        "comparable": comparable,
        "note": "离线占位结果不可用于质量结论；comparable=false 时仅验证对比工具与记录格式",
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="对比两次评测 run")
    ap.add_argument("baseline", type=Path)
    ap.add_argument("candidate", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    result = compare(args.baseline, args.candidate)
    out = args.out or (args.candidate / "comparison.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "baseline": result["baseline_run_id"], "candidate": result["candidate_run_id"],
        "cases": result["case_count"], "guard_pass_rate": result["guard_pass_rate"],
        "guard_failures": result["guard_failures"],
        "comparable": result["comparable"],
        "comparison_hash": stable_hash(result),
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
