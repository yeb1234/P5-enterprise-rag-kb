"""离线 harness 自测：python -m unittest discover -s eval/tests -t ."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.compare_runs import compare  # noqa: E402
from eval.core import contracts, pipeline  # noqa: E402
from eval.core.ids import stable_hash  # noqa: E402
from eval.core.recorder import comparability_hash, validate_record  # noqa: E402
from eval.core.registry import Registry  # noqa: E402
from eval.run_eval import build_ctx, dataset_hash, run_all  # noqa: E402


class Args:
    profile = "offline_placeholder"
    seed = 20261111
    label = "selftest"
    variant = None
    set = []


def make_ctx():
    reg = Registry()
    return build_ctx(reg, Args(), dataset_hash(ROOT))


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry()
        cls.ctx = make_ctx()
        cls.cases = {c["case_id"]: c for c in pipeline.load_cases()}
        cls.records = {r["case_id"]: r for r in run_all(cls.ctx, cls.cases.values())}

    def test_registry_has_14_versioned_objects_and_stable_hash(self):
        self.assertEqual(len(self.registry.objects), 14)
        self.assertEqual(self.registry.config_hash, Registry().config_hash)
        self.assertEqual(len(self.registry.object_refs()), 14)

    def test_contract_gate_blocks_real_api(self):
        gate = contracts.contract_gate(self.registry, "real_api")
        self.assertFalse(gate["ready_for_real_api"])
        self.assertGreater(gate["blocking_pending_count"], 0)
        with self.assertRaises(contracts.ContractNotReady):
            contracts.assert_real_ready(self.registry, "real_api")
        contracts.assert_real_ready(self.registry, "offline_placeholder")

    def test_all_cases_have_six_ordered_steps(self):
        for cid, rec in self.records.items():
            self.assertEqual([s["step"] for s in rec["chain"]], pipeline.STEP_ORDER, cid)
            self.assertEqual(validate_record(rec), [], cid)

    def test_each_step_is_linked_to_object_versions(self):
        for step in self.records["C-001"]["chain"]:
            self.assertTrue(step["object_refs"], step["step"])
            self.assertTrue(all("@" in ref for ref in step["object_refs"]))

    def test_determinism_across_two_runs(self):
        first = run_all(self.ctx, list(self.cases.values()))
        second = run_all(self.ctx, list(self.cases.values()))
        self.assertEqual(comparability_hash(first), comparability_hash(second))

    def test_normal_case_retrieves_gold_evidence(self):
        rec = self.records["C-001"]
        self.assertEqual(rec["final"]["status"], "ok")
        self.assertEqual(rec["final"]["retrieval_metrics"]["recall_at_k"], 1.0)
        self.assertTrue(rec["chain"][3]["metrics"]["prompt_hash"])

    def test_empty_document_has_zero_chunks_and_abstains(self):
        rec = self.records["C-013"]
        self.assertEqual(rec["chain"][0]["metrics"]["chunk_count"], 0)
        self.assertIn("现有资料无法回答", rec["final"]["answer_text"])

    def test_ultra_long_text_is_bounded_and_overflow_recorded(self):
        rec = self.records["C-014"]
        chunk_params = self.registry.content("OBJ-CHUNK-PARAMS")
        self.assertLessEqual(rec["chain"][0]["metrics"]["chunk_count"],
                             chunk_params["max_chunks_per_document"])
        self.assertTrue(rec["chain"][3]["metrics"]["prompt_overflow"])

    def test_fault_scenarios_reach_expected_status(self):
        self.assertEqual(self.records["C-018"]["final"]["status"], "degraded")
        self.assertEqual(self.records["C-019"]["final"]["status"], "degraded")
        self.assertEqual(self.records["C-020"]["final"]["status"], "incomplete")
        self.assertEqual(self.records["C-020"]["final"]["scoring"]["composite_score"], None)
        self.assertTrue(self.records["C-018"]["errors"][0]["code"].startswith("E_PLACEHOLDER"))

    def test_scoring_unavailable_is_not_silently_zero(self):
        scoring = self.records["C-020"]["final"]["scoring"]
        self.assertEqual(scoring["status"], "unavailable")
        self.assertNotIn(0, scoring["checks"].values())

    def test_negative_control_detects_sensitive_leak(self):
        checks = self.records["C-023"]["final"]["scoring"]["checks"]
        self.assertFalse(checks["R5_no_sensitive_pattern_leak"])

    def test_no_raw_prompt_or_secret_in_records(self):
        blob = json.dumps(self.records, ensure_ascii=False)
        self.assertNotIn("raw_prompt", blob)
        self.assertNotIn("credential_value", blob)
        self.assertNotIn("你是企业内部说明书问答助手", blob)

    def test_variant_override_changes_config_hash_only_in_memory(self):
        reg = Registry()
        before = reg.config_hash
        reg.apply_override("OBJ-RETRIEVAL-CFG", "top_k", "3")
        reg.recompute_hash()
        self.assertNotEqual(before, reg.config_hash)
        self.assertEqual(Registry().content("OBJ-RETRIEVAL-CFG")["top_k"], 8)

    def test_comparison_tool_pairs_cases(self):
        runs = sorted(
            (p for p in (ROOT / "eval/results").glob("*/manifest.json")),
            key=lambda p: p.stat().st_mtime,
        )
        if len(runs) < 2:
            self.skipTest("需先运行两次离线评测生成对比 run")
        result = compare(runs[0].parent, runs[-1].parent)
        self.assertEqual(result["case_count"], 26)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["guard_failures"], [])


if __name__ == "__main__":
    unittest.main()
