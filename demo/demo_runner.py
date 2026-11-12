"""一键演示 runner —— 照着念、按命令敲，9 步走完从开场到结果确认。

读取 demo/scenario.json（唯一事实源），真实调用 eval/ 与 demo/ 的工具，
逐步打印【话术 / 操作 / 预期 / 实际 / 对齐】，并把结果写入 demo/out/demo_run_result.json。
不含任何真实 API 调用、不读任何密钥、不发起网络请求。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = DEMO / "out"
RESULTS = ROOT / "eval" / "results"


def load_scenario() -> dict:
    return json.loads((DEMO / "scenario.json").read_text(encoding="utf-8"))


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _call(script: str, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=str(ROOT), env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{script} 退出码 {proc.returncode}: {proc.stdout[-500:]} {proc.stderr[-500:]}")
    return proc.stdout.strip()


def _json_out(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"输出不是 JSON: {text[:300]}")
    return json.loads(text[start:end + 1])


def ck(checks: list, name: str, expected, actual) -> None:
    checks.append({"name": name, "expected": expected, "actual": actual, "passed": expected == actual})


class DemoRunner:
    def __init__(self, scenario: dict):
        self.s = scenario
        self.c = scenario["constants"]
        self.run_id: str | None = None
        self.variant_id: str | None = None

    # ---------- S1 ----------
    def s1(self):
        checks = []
        ck(checks, "场景标识", "DEMO-KBQA-EVAL-001", self.s["scenario_id"])
        ck(checks, "步骤数", 9, len(self.s["steps"]))
        ck(checks, "档位", "offline_placeholder", self.s["profile"])
        actual = {"scenario_id": self.s["scenario_id"], "title": self.s["title"],
                  "goal": self.s["goal"], "boundary": self.s["boundary_statement"],
                  "steps": len(self.s["steps"]), "minutes": self.s["duration_minutes"]}
        return actual, checks, ["场景、目标、边界三者齐备"]

    # ---------- S2 ----------
    def s2(self):
        checks = []
        req = (ROOT / "eval" / "requirements.txt").read_text(encoding="utf-8")
        stdlib_only = "仅使用 Python 标准库" in req
        ck(checks, "纯标准库", True, stdlib_only)
        ck(checks, "零网络", 0, self.c["network_calls"])
        ck(checks, "档位", "offline_placeholder", self.s["profile"])
        actual = {"python": platform.python_version(), "profile": self.s["profile"],
                  "stdlib_only": stdlib_only, "network_calls": 0, "credentials_loaded": False}
        return actual, checks, ["Python " + platform.python_version(), "无第三方依赖"]

    # ---------- S3 ----------
    def s3(self):
        gate = _json_out(_call("eval/run_eval.py", "--check-contracts"))
        checks = []
        ck(checks, "契约槽位总数", self.c["contract_total"], gate["total_fields"])
        ck(checks, "blocking 待确认", self.c["blocking_contract_count"], gate["blocking_pending_count"])
        ck(checks, "optional 待确认", self.c["optional_contract_count"], gate["optional_pending_count"])
        ck(checks, "真实档位未就绪", False, gate["ready_for_real_api"])
        actual = {"total_fields": gate["total_fields"], "blocking": gate["blocking_pending_count"],
                  "optional": gate["optional_pending_count"], "ready_for_real_api": gate["ready_for_real_api"]}
        return actual, checks, ["真实档位被门禁明确阻断"]

    # ---------- S4 ----------
    def s4(self):
        lines = _call("eval/run_eval.py", "--list-objects").splitlines()
        refs = [l.strip() for l in lines if l.strip().startswith("OBJ-")]
        hash_line = next((l for l in lines if l.startswith("config_hash=")), "")
        checks = []
        ck(checks, "版本化对象数", self.c["object_count"], len(refs))
        ck(checks, "配置快照哈希可见", True, hash_line.startswith("config_hash=") and len(hash_line) > 20)
        actual = {"object_count": len(refs), "config_snapshot_hash": hash_line.split("=", 1)[-1],
                  "sample_refs": refs[:3]}
        return actual, checks, [f"{len(refs)} 个 object_id@version", hash_line]

    # ---------- S5 ----------
    def s5(self):
        out = _json_out(_call("eval/run_eval.py"))
        self.run_id = out["run_id"]
        checks = []
        ck(checks, "用例数", self.c["case_count"], out["cases"])
        ck(checks, "状态分布", self.c["status_distribution"], out["status"])
        ck(checks, "闸门结论", self.c["verdict"], out["verdict"])
        ck(checks, "确定性双跑", True, out["determinism_pass"])
        ck(checks, "Stage1 闸门", True, out["stage_1_pass"])
        actual = {"run_id": out["run_id"], "cases": out["cases"], "status": out["status"],
                  "verdict": out["verdict"], "determinism_pass": out["determinism_pass"],
                  "stage_1_pass": out["stage_1_pass"],
                  "blocking_contract_pending": out["blocking_contract_pending"]}
        return actual, checks, [f"run_id={self.run_id}", "23 / 2 / 1", "OFFLINE_READY"]

    # ---------- S6 ----------
    def s6(self):
        rec = _json_out(_call("demo/inspect_record.py", "C-001", "--run", self.run_id, "--json"))
        checks = []
        ck(checks, "六链路步数", self.c["chain_step_count"], len(rec["chain"]))
        ck(checks, "C-001 状态", "ok", rec["status"])
        ck(checks, "recall_at_k", 1.0, rec["recall_at_k"])
        ck(checks, "chunk_count", 4, rec["chunk_count"])
        ck(checks, "带 [1] 引用", True, rec["has_citation"])
        actual = {"case_id": rec["case_id"], "chain_steps": [c["step"] for c in rec["chain"]],
                  "status": rec["status"], "recall_at_k": rec["recall_at_k"],
                  "chunk_count": rec["chunk_count"], "has_citation": rec["has_citation"],
                  "scoring_status": rec["scoring_status"], "prompt_hash": rec["prompt_hash"]}
        return actual, checks, ["六步顺序：" + " → ".join(actual["chain_steps"])]

    # ---------- S7 ----------
    def s7(self):
        ids = ["C-013", "C-018", "C-020", "C-015", "C-016"]
        recs = {cid: _json_out(_call("demo/inspect_record.py", cid, "--run", self.run_id, "--json"))
                for cid in ids}
        checks = []
        c13, c18, c20 = recs["C-013"], recs["C-018"], recs["C-020"]
        ck(checks, "空文档分块数", 0, c13["chunk_count"])
        ck(checks, "空文档拒答", True, c13["abstained"])
        ck(checks, "超时最终状态", "degraded", c18["status"])
        ck(checks, "超时错误留痕", True, any(e["code"] == "E_PLACEHOLDER_TIMEOUT" for e in c18["errors"]))
        ck(checks, "评分不可用最终状态", "incomplete", c20["status"])
        ck(checks, "评分不可用状态", "unavailable", c20["scoring_status"])
        ck(checks, "不静默记 0 分", None, c20["composite_score"])
        ck(checks, "检索为空强制标记", True, recs["C-015"]["forced_empty"])
        ck(checks, "提示词溢出留痕", True, recs["C-016"]["prompt_overflow"])
        actual = {cid: {"status": r["status"], "scenario": r["scenario"],
                        "scoring_status": r["scoring_status"], "chunk_count": r["chunk_count"],
                        "composite_score": r["composite_score"], "errors": [e["code"] for e in r["errors"]]}
                  for cid, r in recs.items()}
        return actual, checks, ["C-013 / C-018 / C-020 三类边界状态收敛正确"]

    # ---------- S8 ----------
    def s8(self):
        variant = _json_out(_call(
            "eval/run_eval.py", "--set", "OBJ-RETRIEVAL-CFG.content.top_k=3", "--label", "exp-topk3"
        ))
        self.variant_id = variant["run_id"]
        base_dir = RESULTS / self.run_id
        variant_dir = RESULTS / self.variant_id
        # 命令本身也是演示动作；正式比对读工具落盘的 comparison.json（字段更全）
        _json_out(_call(
            "eval/compare_runs.py", str(base_dir.relative_to(ROOT)), str(variant_dir.relative_to(ROOT))
        ))
        cmp_out = json.loads((variant_dir / "comparison.json").read_text(encoding="utf-8"))
        checks = []
        ck(checks, "变体闸门结论", self.c["verdict"], variant["verdict"])
        ck(checks, "逐用例配对数", self.c["case_count"], cmp_out["case_count"])
        ck(checks, "占位不可比较", False, cmp_out["comparable"])
        actual = {"variant_run_id": self.variant_id, "case_count": cmp_out["case_count"],
                  "comparable": cmp_out["comparable"], "guard_pass_rate": cmp_out["guard_pass_rate"],
                  "guard_failures": cmp_out["guard_failures"]}
        return actual, checks, [f"comparison.json 已写入 {variant_dir.name}"]

    # ---------- S9 ----------
    def s9(self):
        manifest = json.loads((RESULTS / self.run_id / "manifest.json").read_text(encoding="utf-8"))
        summary = json.loads((RESULTS / self.run_id / "summary.json").read_text(encoding="utf-8"))
        rec0 = json.loads((RESULTS / self.run_id / "records.jsonl").read_text(encoding="utf-8").splitlines()[0])
        checks = []
        ck(checks, "manifest.run_id", self.run_id, manifest["run_id"])
        ck(checks, "配置哈希对齐", rec0["config_snapshot_hash"], manifest["registry_snapshot"]["config_hash"])
        ck(checks, "代码哈希对齐", rec0["code_hash"], manifest["code_hash"])
        ck(checks, "数据集哈希对齐", rec0["dataset_hash"], manifest["dataset_hash"])
        ck(checks, "对象版本数", self.c["object_count"], len(manifest["object_versions"]))
        ck(checks, "零网络", 0, manifest["environment"]["network_calls"])
        ck(checks, "最终结论", self.c["verdict"], summary["verdict"])
        ck(checks, "质量结论", self.c["quality_verdict"], summary["quality_verdict"])
        actual = {"run_id": manifest["run_id"],
                  "config_snapshot_hash": manifest["registry_snapshot"]["config_hash"],
                  "code_hash": manifest["code_hash"], "dataset_hash": manifest["dataset_hash"],
                  "object_versions": len(manifest["object_versions"]),
                  "network_calls": manifest["environment"]["network_calls"],
                  "verdict": summary["verdict"], "quality_verdict": summary["quality_verdict"]}
        return actual, checks, ["演示侧与验证侧四个复现要素逐字段一致"]

    RUNNERS = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5,
               "S6": s6, "S7": s7, "S8": s8, "S9": s9}

    def operations(self, step_id: str) -> str:
        cmds = self.s["commands"]
        return {
            "S1": "读 demo/scenario.json 场景卡（无需命令）",
            "S2": "python demo/demo_runner.py（环境自检内置）",
            "S3": cmds["contracts"], "S4": cmds["objects"], "S5": cmds["full_run"],
            "S6": "python demo/inspect_record.py C-001",
            "S7": "python demo/inspect_record.py C-013 / C-018 / C-020",
            "S8": cmds["variant"] + " ; " + cmds["compare"],
            "S9": f"读 eval/results/<run_id>/manifest.json 与 summary.json",
        }[step_id]


def main() -> int:
    ap = argparse.ArgumentParser(description="一键演示：从开场到结果确认")
    ap.add_argument("--step", default=None, help="只排练某一步，例如 S6（需先跑过全量）")
    ap.add_argument("--quiet", action="store_true", help="只输出 JSON 结果，不打印台本")
    args = ap.parse_args()

    scenario = load_scenario()
    runner = DemoRunner(scenario)
    OUT.mkdir(parents=True, exist_ok=True)
    selected = [s for s in scenario["steps"] if not args.step or s["id"] == args.step]
    results = []

    for step in selected:
        sid = step["id"]
        talk = scenario["talk_lines"][step["talk_key"]]
        operation = runner.operations(sid)
        if not args.quiet:
            print("\n" + "─" * 68)
            print(f"{sid} · {step['phase']} · {step['title']}（约 {step['minutes']} 分钟）")
            print(f"【话术】{talk}")
            print(f"【操作】{operation}")
            print(f"【预期】{' / '.join(step['expected'])}")
        try:
            actual, checks, evidence = runner.RUNNERS[sid](runner)
        except Exception as exc:
            actual, checks, evidence = {"error": str(exc)}, [
                {"name": "步骤执行", "expected": "成功", "actual": str(exc), "passed": False}], []
        aligned = all(c["passed"] for c in checks)
        if not args.quiet:
            print(f"【实际】{json.dumps(actual, ensure_ascii=False, default=str)[:600]}")
            print(f"【对齐】{'✓ 全部一致' if aligned else '✗ 存在不一致'}（{sum(c['passed'] for c in checks)}/{len(checks)} 项）")
            for e in evidence:
                print(f"        · {e}")
            if not aligned:
                for c in checks:
                    if not c["passed"]:
                        print(f"        ! {c['name']}: 预期 {c['expected']} / 实际 {c['actual']}")
        results.append({
            "id": sid, "phase": step["phase"], "title": step["title"],
            "talk": talk, "operation": operation, "expected": step["expected"],
            "actual": actual, "checks": checks, "aligned": aligned, "evidence": evidence,
        })

    final = {
        "verdict": "OFFLINE_READY" if all(r["aligned"] for r in results) else "MISALIGNED",
        "all_aligned": all(r["aligned"] for r in results),
        "run_id": runner.run_id, "variant_run_id": runner.variant_id,
        "status_distribution": scenario["constants"]["status_distribution"],
        "quality_verdict": scenario["constants"]["quality_verdict"],
    }
    payload = {
        "schema": "demo-run/v1.0",
        "scenario_id": scenario["scenario_id"], "scenario_version": scenario["scenario_version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": scenario["profile"], "steps": results, "final": final,
    }
    (OUT / "demo_run_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if not args.quiet:
        print("\n" + "═" * 68)
        print(f"演示结束：{final['verdict']}｜{sum(r['aligned'] for r in results)}/{len(results)} 步对齐"
              f"｜run_id={runner.run_id}")
        print(f"结果文件：demo/out/demo_run_result.json")
        print(f"结论：{scenario['boundary_statement']}")
    return 0 if final["all_aligned"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
