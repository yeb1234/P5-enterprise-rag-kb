#!/usr/bin/env python3
"""用当前评分口径重算已有 run，生成**新的 run_id**（不原地修改任何已产生结果）。

用法：python evalbench/rescore.py RUN-xxxxxxxxxxxx
旧 run 完整保留，新 run 的 config_snapshot 记录 rescored_from 与评分版本，便于前后对照。
重评分只读取已落盘的原始 answer，不重发任何 API 请求。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from evalbench.core import gitmeta, runner, scoring  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python evalbench/rescore.py <run_id>")
        return 2
    src_id = sys.argv[1]
    src_dir = ROOT / "runs" / src_id
    if not src_dir.exists():
        print(f"run not found: {src_id}")
        return 2

    samples = {s["id"]: s for s in runner.load_samples()}
    records = [json.loads(line) for line in
               (src_dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    old_snapshot = json.loads((src_dir / "config_snapshot.json").read_text(encoding="utf-8"))
    old_summary = json.loads((src_dir / "summary.json").read_text(encoding="utf-8"))

    changed = 0
    for r in records:
        sample = samples[r["sample_id"]]
        new_scores = scoring.score_sample(
            sample, r.get("answer", ""),
            {"latency_ms": (r.get("scores") or {}).get("latency_ms", 0)},
        )
        if new_scores != r.get("scores"):
            changed += 1
        r["scores"] = new_scores
        r["scoring_revision"] = scoring.load_metrics()["version"]

    new_snapshot = dict(old_snapshot)
    git = gitmeta.snapshot()
    new_snapshot.update({
        "rescored_from": src_id,
        "rescored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scoring_revision": scoring.load_metrics()["version"],
        "scoring_revision_from": old_snapshot.get("metrics_version", "METRICS@1.0.0"),
        "git": git,
        "note": "原始 answer 未改动，仅重算 scores；旧 run 完整保留作为前后对照",
    })
    new_snapshot["metrics_version"] = scoring.load_metrics()["version"]
    new_id = "RUN-" + hashlib.sha256(
        json.dumps([src_id, new_snapshot["scoring_revision"], time.time_ns()],
                   ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    new_snapshot["run_id"] = new_id

    summary = scoring.aggregate(records)
    summary.update({
        "run_id": new_id,
        "label": old_summary.get("label", "") + " (rescored)",
        "mode": old_summary.get("mode"),
        "comparable": old_summary.get("comparable"),
        "git": git,
        "n_records": len(records),
        "status_counts": {s: sum(1 for r in records if r["status"] == s)
                          for s in sorted({r["status"] for r in records})},
        "rescored_from": src_id,
        "scoring_revision": scoring.load_metrics()["version"],
        "records_changed": changed,
    })

    out_dir = ROOT / "runs" / new_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config_snapshot.json").write_text(
        json.dumps(new_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "records.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "source_run": src_id, "new_run": new_id,
        "scoring_revision": summary["scoring_revision"],
        "records_changed": changed, "n_records": len(records),
        "by_model": summary["by_model"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
