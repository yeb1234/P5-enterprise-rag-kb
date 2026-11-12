#!/usr/bin/env python3
"""重建所有 run 的派生 summary.json（聚合 bug 修复后使用）。

只重建派生汇总；records.jsonl（原始回答与逐条分数）与 config_snapshot.json 一字不动，
因此任何 run 都可随时从 records 完全重算，可追溯性不受影响。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from evalbench.core import scoring  # noqa: E402


def main() -> int:
    touched = []
    for d in sorted((ROOT / "runs").glob("RUN-*")):
        rp, sp = d / "records.jsonl", d / "summary.json"
        if not (rp.exists() and sp.exists()):
            continue
        records = [json.loads(line) for line in rp.read_text(encoding="utf-8").splitlines() if line.strip()]
        old = json.loads(sp.read_text(encoding="utf-8"))
        agg = scoring.aggregate(records)
        agg.update({k: old[k] for k in ("run_id", "label", "mode", "comparable", "git")
                    if k in old})
        agg["n_records"] = len(records)
        agg["status_counts"] = {s: sum(1 for r in records if r["status"] == s)
                                for s in sorted({r["status"] for r in records})}
        for k in ("rescored_from", "scoring_revision", "records_changed"):
            if k in old:
                agg[k] = old[k]
        sp.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
        touched.append(d.name)
    print(json.dumps({"rebuilt": len(touched), "runs": touched,
                      "note": "仅派生 summary；records 与 config_snapshot 未改动"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
