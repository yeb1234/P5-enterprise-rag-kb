"""离线占位评测入口（仅 Python 标准库，无需任何真实 API / 网络 / 密钥）。

用法：
  python eval/run_eval.py                          # 全量离线用例 + 确定性双跑 + 闸门判定
  python eval/run_eval.py --check-contracts        # 列出全部 __TBC__ 接入契约待确认项
  python eval/run_eval.py --list-objects           # 列出所有版本化对象
  python eval/run_eval.py --set OBJ-RETRIEVAL-CFG.content.top_k=4 --label exp-topk4   # 对比实验变体
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.core import contracts, pipeline  # noqa: E402
from eval.core.ids import (  # noqa: E402
    code_hash, make_experiment_id, make_run_id, sha256_file, stable_hash,
)
from eval.core.metrics import step_latency_summary  # noqa: E402
from eval.core.recorder import (  # noqa: E402
    Recorder, comparability_hash, evaluate_offline_gate, strip_volatile, validate_record,
)
from eval.core.registry import EVAL_ROOT, Registry  # noqa: E402


def dataset_hash(root: Path) -> str:
    base = root if (root / "datasets").exists() else root / "eval"
    cases = base / "datasets" / "cases_v0.1.0.jsonl"
    fixtures = base / "datasets" / "fixtures_v0.1.0.json"
    return stable_hash({"cases": sha256_file(cases), "fixtures": sha256_file(fixtures)})


def harness_hash() -> str:
    return code_hash(Path(__file__).resolve().parent)


def build_ctx(registry: Registry, args, dhash: str) -> dict:
    config_hash = registry.config_hash
    chash = harness_hash()
    variant_id = args.variant or ("control" if not args.set else "variant-" + stable_hash(args.set)[:8])
    run_id = make_run_id(args.profile, args.seed, config_hash, dhash, args.label, chash)
    return {
        "registry": registry,
        "fixtures": pipeline.load_fixtures(),
        "profile": args.profile,
        "seed": args.seed,
        "run_id": run_id,
        "config_hash": config_hash,
        "code_hash": chash,
        "dataset_hash": dhash,
        "dataset_version": registry.get("OBJ-DATASET")["version"],
        "experiment_id": make_experiment_id(variant_id, config_hash),
        "variant_id": variant_id,
    }


def run_all(ctx: dict, cases: list[dict]) -> list[dict]:
    return [pipeline.run_case(case, ctx) for case in cases]


def main() -> int:
    parser = argparse.ArgumentParser(description="说明书分块问答评测 · 离线占位链路")
    parser.add_argument("--profile", default="offline_placeholder",
                        choices=["offline_placeholder", "real_api_pending", "real_api"])
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--label", default="default")
    parser.add_argument("--variant", default=None)
    parser.add_argument("--out", default=str(EVAL_ROOT / "results"))
    parser.add_argument("--set", action="append", default=[],
                        metavar="OBJECT_ID.content.path=JSON_VALUE",
                        help="实验变体：仅内存覆盖，不修改版本化对象文件；采纳后须新建对象版本")
    parser.add_argument("--check-contracts", action="store_true")
    parser.add_argument("--list-objects", action="store_true")
    parser.add_argument("--skip-determinism", action="store_true")
    args = parser.parse_args()

    registry = Registry()

    if args.list_objects:
        for ref in registry.object_refs():
            print(ref)
        print(f"config_hash={registry.config_hash}")
        return 0

    gate = contracts.contract_gate(registry, args.profile)
    if args.check_contracts:
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 0

    if args.profile == "real_api":
        try:
            contracts.assert_real_ready(registry, args.profile)
        except contracts.ContractNotReady as exc:
            print(f"[BLOCK] {exc}")
            print(json.dumps(gate["pending"], ensure_ascii=False, indent=2))
            return 2

    overrides = []
    for spec in args.set:
        if "=" not in spec:
            parser.error(f"--set 格式应为 OBJECT_ID.content.path=JSON_VALUE，收到 {spec}")
        left, raw = spec.split("=", 1)
        parts = left.split(".")
        if len(parts) < 3 or parts[1] != "content":
            parser.error(f"--set 路径必须以 .content. 开头，收到 {left}")
        overrides.append(registry.apply_override(parts[0], ".".join(parts[2:]), raw))
        registry.recompute_hash()

    dhash = dataset_hash(EVAL_ROOT)
    ctx = build_ctx(registry, args, dhash)
    cases = pipeline.load_cases()
    records = run_all(ctx, cases)
    second = [] if args.skip_determinism else run_all(ctx, cases)
    determinism_pass = True if args.skip_determinism else comparability_hash(records) == comparability_hash(second)

    thresholds_obj = registry.content("OBJ-THRESHOLDS")
    gate_result = evaluate_offline_gate(records, thresholds_obj)
    gate_result["checks"].append({
        "metric": "determinism_pass", "op": "==", "expected": True,
        "actual": determinism_pass, "passed": determinism_pass,
    })
    gate_result["stage_1_pass"] = all(c["passed"] for c in gate_result["checks"])
    verdict = "OFFLINE_READY" if gate_result["stage_1_pass"] else "BLOCK"

    recorder = Recorder(Path(args.out), ctx["run_id"])
    recorder.write_records(records)
    summary = {
        "run_id": ctx["run_id"],
        "verdict": verdict,
        "quality_verdict": "NOT_COMPARABLE",
        "profile": args.profile,
        "case_count": len(records),
        "status_distribution": dict(Counter(r["final"]["status"] for r in records)),
        "suite_distribution": dict(Counter(r["suite"] for r in records)),
        "scenario_distribution": dict(Counter(r["scenario"] for r in records)),
        "fault_handled_correct_rate": round(
            sum(1 for r in records if r["final"]["fault_handled_correct"]) / max(len(records), 1), 4
        ),
        "comparability_hash": comparability_hash(records),
        "determinism_pass": determinism_pass,
        "gate": gate_result,
        "note": "占位结果不可与真实 API 结果比较；质量阈值冻结前不得判 PASS",
    }
    manifest = {
        "manifest_schema": "eval-manifest/v0.1",
        "run_id": ctx["run_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "seed": args.seed,
        "label": args.label,
        "experiment_id": ctx["experiment_id"],
        "variant_id": ctx["variant_id"],
        "variant_overrides": overrides,
        "variant_promotion_rule": "变体仅在内存中生效；采纳后必须新建对应对象版本并写变更记录",
        "registry_snapshot": registry.snapshot(),
        "code_hash": ctx["code_hash"],
        "dataset_hash": dhash,
        "dataset_ref": "OBJ-DATASET@" + registry.get("OBJ-DATASET")["version"],
        "baseline_ref": "OBJ-BASELINE@" + registry.get("OBJ-BASELINE")["version"],
        "threshold_ref": "OBJ-THRESHOLDS@" + registry.get("OBJ-THRESHOLDS")["version"],
        "object_versions": registry.versions(),
        "chain_order": pipeline.STEP_ORDER,
        "environment": {
            "python": platform.python_version(),
            "network_calls": 0,
            "external_dependencies": "none (stdlib only)",
            "credentials_loaded": False,
        },
        "contract_gate": {
            "ready_for_real_api": gate["ready_for_real_api"],
            "blocking_pending_count": gate["blocking_pending_count"],
            "optional_pending_count": gate["optional_pending_count"],
        },
        "per_case_hash": {r["case_id"]: stable_hash(strip_volatile(r)) for r in records},
        "latency_by_step": step_latency_summary(records),
        "schema_validation_errors": {r["case_id"]: validate_record(r) for r in records if validate_record(r)},
        "summary": summary,
    }
    recorder.write_json("manifest.json", manifest)
    recorder.write_json("summary.json", summary)
    recorder.write_json("contract_gate.json", gate)

    print(json.dumps({
        "run_id": ctx["run_id"], "verdict": verdict,
        "cases": len(records), "status": summary["status_distribution"],
        "determinism_pass": determinism_pass,
        "stage_1_pass": gate_result["stage_1_pass"],
        "blocking_contract_pending": gate["blocking_pending_count"],
        "results_dir": str(recorder.path),
    }, ensure_ascii=False, indent=2))
    return 0 if verdict == "OFFLINE_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
