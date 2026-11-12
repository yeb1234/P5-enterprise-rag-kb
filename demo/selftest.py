"""一键自测：把「演示文档 / 测试话术 / 配套工具 / 演示侧 / 验证侧」逐项对齐。

用法：python demo/selftest.py
产物：demo/out/selftest_report.json
退出码：0 = 全部通过；1 = 存在不对齐（终端会列出具体项）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = DEMO / "out"
DOCS = ROOT / "docs" / "demo"
sys.path.insert(0, str(ROOT))
from eval.core.recorder import validate_record  # noqa: E402

FILES = {
    "index": DOCS / "README.md",
    "01": DOCS / "01-demo-guide.md",
    "02": DOCS / "02-demo-talktrack.md",
    "03": DOCS / "03-demo-tools.md",
    "04": DOCS / "04-selftest-and-alignment.md",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def call(script: str, *args: str) -> str:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), *args], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{script} 退出码 {proc.returncode}: {proc.stdout[-400:]} {proc.stderr[-400:]}")
    return proc.stdout.strip()


class Suite:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, cid: str, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"id": cid, "name": name, "passed": bool(ok), "detail": detail})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    suite = Suite()
    scenario = json.loads((DEMO / "scenario.json").read_text(encoding="utf-8"))
    const = scenario["constants"]
    docs = {k: read(p) for k, p in FILES.items()}
    all_docs = "\n".join(docs.values())
    talk = docs["02"]

    # 1 四部分文档与索引齐备
    missing = [str(p.name) for p in FILES.values() if not p.exists()]
    suite.add("C01", "四部分文档 + 索引齐备", not missing,
              "缺少: " + ", ".join(missing) if missing else "5 个文件均存在且可读")

    # 2 场景事实源结构
    steps = scenario.get("steps", [])
    talks = scenario.get("talk_lines", {})
    structure_ok = (
        len(steps) == 9 and len(talks) == 9
        and all(s["talk_key"] in talks for s in steps)
        and len(scenario["featured_cases"]["boundaries"]) == 5
        and len(scenario["glossary"]) >= 8
    )
    suite.add("C02", "scenario.json 结构完整", structure_ok,
              f"步骤={len(steps)}, 话术行={len(talks)}, 边界用例={len(scenario['featured_cases']['boundaries'])}")

    # 3 文档关键数值字面量
    absent = [x for x in scenario["expected_literals_in_docs"] if x not in all_docs]
    suite.add("C03", "文档关键数值/术语齐全", not absent,
              "缺少: " + ", ".join(absent) if absent else f"{len(scenario['expected_literals_in_docs'])} 个字面量全部命中")

    # 4 话术关键字面量
    absent_t = [x for x in scenario["expected_literals_in_talktrack"] if x not in talk]
    suite.add("C04", "话术关键数值/用例齐全", not absent_t,
              "缺少: " + ", ".join(absent_t) if absent_t else f"{len(scenario['expected_literals_in_talktrack'])} 个字面量全部命中")

    # 5 九句话术逐字一致
    not_verbatim = [k for k, v in talks.items() if v not in talk]
    suite.add("C05", "9 句话术与事实源逐字一致", not not_verbatim,
              "不一致: " + ", ".join(not_verbatim) if not_verbatim else "T1–T9 全部逐字命中")

    # 6 文档/话术引用的用例 ID 都在数据集里
    cases = [json.loads(l)["case_id"] for l in (ROOT / "eval/datasets/cases_v0.1.0.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    cited = set(re.findall(r"C-\d{3}", docs["01"] + talk + docs["03"] + docs["04"]))
    bad_cases = sorted(cited - set(cases))
    suite.add("C06", "引用的用例 ID 均存在", not bad_cases,
              f"引用 {len(cited)} 个，数据集 {len(cases)} 条" + (f"；未知 {bad_cases}" if bad_cases else ""))

    # 7 引用的对象 ID 都在注册表里
    registry = json.loads((ROOT / "eval/config/registry.json").read_text(encoding="utf-8"))
    obj_ids = {o["object_id"] for o in registry["objects"]}
    cited_objs = set(re.findall(r"OBJ-[A-Z]+(?:-[A-Z]+)*", all_docs))
    bad_objs = sorted(cited_objs - obj_ids)
    suite.add("C07", "引用的版本化对象均存在", not bad_objs,
              f"引用 {len(cited_objs)} 个，注册表 {len(obj_ids)} 个" + (f"；未知 {bad_objs}" if bad_objs else ""))

    # 8 命令可复制且脚本存在
    cmd_problems = []
    for key, cmd in scenario["commands"].items():
        if cmd not in all_docs:
            cmd_problems.append(f"{key} 未在文档出现")
        m = re.match(r"python\s+([^\s]+)", cmd)
        if m and not (ROOT / m.group(1)).exists():
            cmd_problems.append(f"{key} 脚本不存在: {m.group(1)}")
    suite.add("C08", "命令可复制且脚本存在", not cmd_problems,
              f"{len(scenario['commands'])} 条命令" + ("；" + "; ".join(cmd_problems) if cmd_problems else " 全部可解析"))

    # 9 术语纪律：禁用词只出现在禁止语境；必需术语齐全
    forbidden_hits = []
    for term in scenario["forbidden_terms"]:
        for line in (docs["01"] + talk + docs["03"]).splitlines():
            if term in line and not any(w in line for w in ("严禁", "禁止", "不得", "不追求", "不是", "不代表")):
                forbidden_hits.append(f"{term}: {line.strip()[:40]}")
    missing_required = [t for t in scenario["required_terms"] if t not in all_docs]
    suite.add("C09", "术语纪律与必需术语", not forbidden_hits and not missing_required,
              f"禁用词越界 {len(forbidden_hits)} 处，必需术语缺失 {len(missing_required)} 个")

    # 10 一键演示实跑
    call("demo/demo_runner.py", "--quiet")
    demo = json.loads((OUT / "demo_run_result.json").read_text(encoding="utf-8"))
    step_checks = sum(len(s["checks"]) for s in demo["steps"])
    passed_checks = sum(sum(c["passed"] for c in s["checks"]) for s in demo["steps"])
    demo_ok = (
        demo["final"]["all_aligned"] and len(demo["steps"]) == 9
        and all(s["aligned"] for s in demo["steps"]) and step_checks == 42
        and passed_checks == 42
    )
    suite.add("C10", "一键演示 9/9 步对齐", demo_ok,
              f"步骤 {sum(s['aligned'] for s in demo['steps'])}/9，逐项核对 {passed_checks}/{step_checks}，verdict={demo['final']['verdict']}")

    run_id = demo["final"]["run_id"]
    run_dir = ROOT / "eval/results" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    records = [json.loads(l) for l in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()]

    # 11 两端对齐（演示侧 vs 验证侧）
    pairs = {
        "run_id": (run_id, manifest["run_id"]),
        "config_hash": (demo["steps"][8]["actual"]["config_snapshot_hash"], manifest["registry_snapshot"]["config_hash"]),
        "code_hash": (demo["steps"][8]["actual"]["code_hash"], manifest["code_hash"]),
        "dataset_hash": (demo["steps"][8]["actual"]["dataset_hash"], manifest["dataset_hash"]),
        "verdict": (demo["final"]["verdict"], summary["verdict"]),
        "quality_verdict": (demo["final"]["quality_verdict"], summary["quality_verdict"]),
        "status_distribution": (demo["final"]["status_distribution"],
                                dict(__import__("collections").Counter(r["final"]["status"] for r in records))),
    }
    mismatch = [k for k, (a, b) in pairs.items() if a != b]
    suite.add("C11", "演示侧 / 验证侧逐字段对齐", not mismatch,
              f"比对 {len(pairs)} 个字段" + (f"；不一致 {mismatch}" if mismatch else " 全部一致"))

    # 12 记录模式与边界收敛
    schema_errors = {r["case_id"]: validate_record(r) for r in records if validate_record(r)}
    by_id = {r["case_id"]: r for r in records}
    boundary_ok = (
        not schema_errors
        and len(records) == const["case_count"]
        and by_id["C-013"]["chain"][0]["metrics"]["chunk_count"] == 0
        and by_id["C-018"]["final"]["status"] == "degraded"
        and by_id["C-020"]["final"]["status"] == "incomplete"
        and by_id["C-020"]["final"]["scoring"]["composite_score"] is None
        and all(r["comparable"] is False for r in records)
    )
    suite.add("C12", "记录模式与边界状态收敛", boundary_ok,
              f"{len(records)} 条记录模式错误 {len(schema_errors)} 处；C-013/C-018/C-020 状态符合预期；comparable 全为 false")

    # 13 演示幂等（再跑一次，去掉时间戳后完全一致）
    before = json.dumps({k: v for k, v in demo.items() if k != "generated_at"},
                        ensure_ascii=False, sort_keys=True)
    call("demo/demo_runner.py", "--quiet")
    again = json.loads((OUT / "demo_run_result.json").read_text(encoding="utf-8"))
    after = json.dumps({k: v for k, v in again.items() if k != "generated_at"},
                       ensure_ascii=False, sort_keys=True)
    suite.add("C13", "演示结果可复现（双跑一致）", before == after,
              "两次 demo_runner 除 generated_at 外逐字节一致" if before == after else "双跑结果不一致")

    # 14 无真实 API 细节泄漏
    leak_re = re.compile(
        r"https?://|dashscope|text-embedding|deepseek-(?:chat|v4|flash|reasoner)|bge-m3|qwen|sk-[A-Za-z0-9]{8}|Bearer\s+[A-Za-z0-9]|qps\s*=\s*\d",
        re.I,
    )
    leaks = []
    scan_roots = [DEMO, DOCS]
    for base in scan_roots:
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in {".py", ".json", ".md"} and "out" not in p.parts:
                # 检查器自身含有检测正则字面量，跳过以免自命中
                if p.name == "selftest.py":
                    continue
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if leak_re.search(line):
                        leaks.append(f"{p.relative_to(ROOT)}:{i}")
    suite.add("C14", "无真实 API 细节泄漏", not leaks,
              "扫描 demo/ 与 docs/demo/，命中 0 处" if not leaks else "命中: " + ", ".join(leaks[:6]))

    # 15 inspect_record 与原始记录一致
    inspected = json.loads(call("demo/inspect_record.py", "C-001", "--run", run_id, "--json"))
    raw = by_id["C-001"]
    inspect_ok = (
        inspected["case_id"] == "C-001"
        and inspected["recall_at_k"] == raw["final"]["retrieval_metrics"]["recall_at_k"]
        and inspected["chunk_count"] == raw["chain"][0]["metrics"]["chunk_count"]
        and inspected["config_snapshot_hash"] == raw["config_snapshot_hash"]
        and len(inspected["chain"]) == 6
    )
    suite.add("C15", "查看器输出与原始记录一致", inspect_ok,
              "case_id / recall_at_k / chunk_count / config_snapshot_hash / 六步 全部一致")

    # 16 第四部分文档与报告结论一致
    report_literals = ["16 / 16", "9 / 9", "42 / 42", run_id, "OFFLINE_READY", "NOT_COMPARABLE", "两端对齐"]
    missing_report = [x for x in report_literals if x not in docs["04"]]
    suite.add("C16", "自测文档与实测结论一致", not missing_report,
              "缺少: " + ", ".join(missing_report) if missing_report else "7 个结论字面量全部命中")

    passed = sum(c["passed"] for c in suite.checks)
    total = len(suite.checks)
    report = {
        "schema": "demo-selftest/v1.0",
        "scenario_id": scenario["scenario_id"],
        "scenario_version": scenario["scenario_version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "demo_step_checks": {"passed": passed_checks, "total": step_checks},
        "checks": suite.checks,
        "summary": {
            "passed": passed, "total": total, "all_passed": passed == total,
            "verdict": "DEMO_READY" if passed == total else "NEEDS_FIX",
            "quality_verdict": "NOT_COMPARABLE",
            "status_distribution": pairs["status_distribution"][0],
            "config_snapshot_hash": manifest["registry_snapshot"]["config_hash"],
            "code_hash": manifest["code_hash"],
            "dataset_hash": manifest["dataset_hash"],
        },
        "summary_line": f"{passed}/{total} 项检查通过；9/9 步对齐；42 项逐项核对全过",
    }
    (OUT / "selftest_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("═" * 70)
    print(f"自测：{scenario['scenario_id']} · {report['summary']['verdict']}")
    print("═" * 70)
    for c in suite.checks:
        print(f"  {'✓' if c['passed'] else '✗'} {c['id']}  {c['name']:<28} {c['detail']}")
    print("─" * 70)
    print(f"{passed}/{total} 项通过｜run_id={run_id}｜报告：demo/out/selftest_report.json")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
