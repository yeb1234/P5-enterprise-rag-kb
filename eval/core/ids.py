"""确定性标识与哈希：同 seed + 同配置 + 同数据集 → 完全一致的 run_id 与逐用例输出。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def stable_hash(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def code_hash(eval_dir: Path) -> str:
    """把 harness 自身代码纳入复现主键：改了代码却复用同一个 run_id 会静默覆盖历史结果。"""
    skip = {"results", "tests", "__pycache__"}
    files = sorted(
        p for p in Path(eval_dir).rglob("*.py")
        if not any(part in skip for part in p.parts)
    )
    return stable_hash([
        {"path": p.relative_to(eval_dir).as_posix(), "sha256": sha256_file(p)}
        for p in files
    ])


def make_run_id(profile: str, seed: int, config_hash: str, dataset_hash: str,
                label: str = "default", harness_hash: str = "") -> str:
    digest = stable_hash({
        "profile": profile, "seed": seed, "config": config_hash,
        "dataset": dataset_hash, "label": label, "code": harness_hash,
    })
    return "RUN-" + digest[:12]


def make_trace_id(run_id: str, case_id: str) -> str:
    return f"{run_id}:{case_id}"


def make_span_id(trace_id: str, step: str) -> str:
    return stable_hash({"trace": trace_id, "step": step})[:16]


def make_experiment_id(variant: str, config_hash: str) -> str:
    return "EXP-" + stable_hash({"variant": variant, "config": config_hash})[:10]
