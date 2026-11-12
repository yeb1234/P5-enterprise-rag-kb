"""统一结果记录、快照比对与离线闸门判定。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .ids import stable_hash
from .metrics import step_latency_summary
from .pipeline import STEP_ORDER

VOLATILE_KEYS = {"duration_ms", "generated_at", "started_at", "ended_at", "walltime_ms", "run_walltime_ms"}
REQUIRED_RECORD_KEYS = {
    "schema_version", "run_id", "trace_id", "case_run_id", "case_id", "case_version",
    "suite", "scenario", "profile", "seed", "object_versions", "config_snapshot_hash",
    "dataset_hash", "code_hash", "chain", "final", "errors", "observability", "placeholder", "comparable",
}
FORBIDDEN_LOG_KEYS = {"raw_prompt", "api_key", "authorization", "secret", "credential_value"}


def strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(x) for x in obj]
    return obj


def comparability_hash(records: list[dict]) -> str:
    """排除耗时/时间戳后的确定性哈希，用于双跑复现比对。"""
    return stable_hash([strip_volatile(r) for r in records])


def validate_record(rec: dict) -> list[str]:
    problems = []
    missing = REQUIRED_RECORD_KEYS - set(rec)
    if missing:
        problems.append(f"缺少字段 {sorted(missing)}")
    steps = [s.get("step") for s in rec.get("chain", [])]
    if steps != STEP_ORDER:
        problems.append(f"链路步骤不完整或顺序错误: {steps}")
    for s in rec.get("chain", []):
        if not s.get("object_refs"):
            problems.append(f"步骤 {s.get('step')} 未关联对象版本")
    if not rec.get("observability", {}).get("trace_fields", {}).get("config_snapshot_hash"):
        problems.append("缺少可观测关联字段 config_snapshot_hash")
    return problems


def _contains_forbidden(obj) -> bool:
    if isinstance(obj, dict):
        return any(k.lower() in FORBIDDEN_LOG_KEYS or _contains_forbidden(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_contains_forbidden(x) for x in obj)
    return False


def evaluate_offline_gate(records: list[dict], thresholds: dict) -> dict:
    total = max(len(records), 1)
    complete = sum(1 for r in records if [s["step"] for s in r["chain"]] == STEP_ORDER)
    schema_ok = sum(1 for r in records if not validate_record(r))
    fault_ok = sum(1 for r in records if r["final"]["fault_handled_correct"])
    unhandled = sum(
        1 for r in records
        for e in r["errors"]
        if not str(e.get("code", "")).startswith("E_PLACEHOLDER")
    )
    no_forbidden = not any(_contains_forbidden(r) for r in records)
    actual = {
        "chain_step_completeness_rate": round(complete / total, 4),
        "record_schema_valid_rate": round(schema_ok / total, 4),
        "fault_scenario_handled_rate": round(fault_ok / total, 4),
        "no_network_calls": all(r["profile"] == "offline_placeholder" for r in records),
        "no_secret_or_raw_prompt_in_logs": no_forbidden,
        "unhandled_exception_count": unhandled,
    }
    checks = []
    for item in thresholds["stage_1_offline_link_health"]["thresholds"]:
        metric, op, expected = item["metric"], item["op"], item["value"]
        if metric == "determinism_pass":
            continue  # 由 run_eval 双跑比对后注入
        value = actual.get(metric)
        passed = (
            value >= expected if op == ">=" else
            value <= expected if op == "<=" else
            value == expected
        )
        checks.append({"metric": metric, "op": op, "expected": expected,
                       "actual": value, "passed": bool(passed)})
    return {
        "actual": actual,
        "checks": checks,
        "stage_1_pass": all(c["passed"] for c in checks),
        "stage_2_status": thresholds["stage_2_real_quality"]["status"],
        "quality_verdict": "NOT_COMPARABLE",
        "note": "离线闸门只判定链路健康；质量阈值未冻结，禁止判 PASS",
    }


class Recorder:
    def __init__(self, out_dir: Path, run_id: str):
        self.path = Path(out_dir) / run_id
        self.path.mkdir(parents=True, exist_ok=True)

    def write_records(self, records: list[dict]) -> Path:
        p = self.path / "records.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return p

    def write_json(self, name: str, data: dict) -> Path:
        p = self.path / name
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return p
