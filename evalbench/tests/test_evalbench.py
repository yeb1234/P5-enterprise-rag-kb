"""evalbench 自检：评分口径、上下文组装、资产完整性、聚合逻辑。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from evalbench.core import runner, scoring  # noqa: E402


class TestScoring(unittest.TestCase):
    def test_normalize_chinese_spacing_and_case(self):
        self.assertEqual(scoring.normalize("长按 3 秒"), scoring.normalize("长按3秒"))
        self.assertEqual(scoring.normalize("2.4GHz Wi-Fi"), scoring.normalize("2.4ghzwifi"))

    def test_answer_accuracy_partial(self):
        s = {"key_facts": ["3 秒", "2.4GHz"], "forbidden_facts": [], "expected_behavior": "answer"}
        got = scoring.score_sample(s, "长按 3 秒后选择 2.4GHz Wi-Fi")
        self.assertEqual(got["answer_accuracy"], 1.0)
        got2 = scoring.score_sample(s, "长按 3 秒即可")
        self.assertEqual(got2["answer_accuracy"], 0.5)

    def test_rejection_correctness(self):
        abstain = {"key_facts": [], "forbidden_facts": [], "expected_behavior": "abstain"}
        answer = {"key_facts": ["3 秒"], "forbidden_facts": [], "expected_behavior": "answer"}
        self.assertEqual(scoring.score_sample(abstain, "INSUFFICIENT")["rejection_correctness"], 1.0)
        self.assertEqual(scoring.score_sample(abstain, "大概是 5 秒")["rejection_correctness"], 0.0)
        self.assertEqual(scoring.score_sample(answer, "3 秒")["rejection_correctness"], 1.0)
        self.assertEqual(scoring.score_sample(answer, "资料不足，无法回答")["rejection_correctness"], 0.0)

    def test_faithfulness_flags_noise_leak(self):
        s = {"key_facts": ["3 秒"], "forbidden_facts": ["24 个月", "延保"], "expected_behavior": "answer"}
        self.assertEqual(scoring.score_sample(s, "长按 3 秒；延保 24 个月")["faithfulness"], 0.0)
        self.assertEqual(scoring.score_sample(s, "长按 3 秒")["faithfulness"], 1.0)

    def test_format_compliance_rejects_think_tags(self):
        s = {"key_facts": [], "forbidden_facts": [], "expected_behavior": "answer"}
        self.assertEqual(scoring.score_sample(s, "<mm:think>考虑一下</mm:think>3 秒")["format_compliance"], 0.0)
        self.assertEqual(scoring.score_sample(s, "")["format_compliance"], 0.0)
        self.assertEqual(scoring.score_sample(s, "3 秒")["format_compliance"], 1.0)

    def test_aggregate_groups_by_model_category(self):
        records = []
        for model in ("m1", "m2"):
            for cat in ("grounded_extraction", "negative_rejection"):
                records.append({
                    "model": model, "prompt_version": "v1", "params_id": "A",
                    "task_category": cat, "status": "ok",
                    "scores": {"answer_accuracy": 1.0, "rejection_correctness": 1.0,
                               "faithfulness": 1.0, "format_compliance": 1.0, "latency_ms": 10},
                })
            records.append({"model": model, "prompt_version": "v1", "params_id": "A",
                            "task_category": "grounded_extraction", "status": "rate_limited", "scores": {}})
        agg = scoring.aggregate(records)
        self.assertEqual(agg["scored_records"], 4)
        self.assertEqual(agg["skipped_records"], 2)
        self.assertEqual(agg["by_model"]["m1"]["overall"], 1.0)


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.samples = runner.load_samples()
        self.params = runner.load_params("A")

    def test_context_noise_rate_and_passage_num(self):
        sample = next(s for s in self.samples if s["id"] == "S04")
        ctx = runner.build_context(sample, self.params)
        self.assertLessEqual(len(ctx), self.params["passage_num"])
        self.assertTrue(any(p["kind"] == "noisy" for p in ctx))
        self.assertTrue(any(p["kind"] == "positive" for p in ctx))

    def test_negative_rejection_is_all_noisy(self):
        sample = next(s for s in self.samples if s["task_category"] == "negative_rejection")
        ctx = runner.build_context(sample, self.params)
        self.assertTrue(ctx)
        self.assertTrue(all(p["kind"] == "noisy" for p in ctx))

    def test_params_c_changes_context(self):
        a = runner.build_context(next(s for s in self.samples if s["id"] == "S04"), runner.load_params("A"))
        c = runner.build_context(next(s for s in self.samples if s["id"] == "S04"), runner.load_params("C"))
        self.assertLessEqual(len(c), 3)
        self.assertNotEqual(len(a), len(c))

    def test_render_prompt_replaces_placeholders(self):
        tpl, _ = runner.load_prompt("v1_baseline")
        out = runner.render_prompt(tpl, self.samples[0], self.samples[0]["passages"])
        self.assertNotIn("{passages}", out)
        self.assertNotIn("{question}", out)
        self.assertIn(self.samples[0]["question"], out)
        self.assertIn("[P01]", out)

    def test_prompt_versions_all_have_placeholders(self):
        for v in ("v1_baseline", "v2_grounded", "v3_structured"):
            tpl, digest = runner.load_prompt(v)
            self.assertIn("{passages}", tpl)
            self.assertIn("{question}", tpl)
            self.assertEqual(len(digest), 16)


class TestAssets(unittest.TestCase):
    def test_samples_shape(self):
        samples = runner.load_samples()
        self.assertEqual(len(samples), 12)
        cats = {}
        for s in samples:
            for key in ("id", "task_category", "question", "passages", "reference_answer",
                        "key_facts", "forbidden_facts", "expected_behavior", "source_basis"):
                self.assertIn(key, s, f"{s.get('id')} 缺字段 {key}")
            cats[s["task_category"]] = cats.get(s["task_category"], 0) + 1
            self.assertIn(s["expected_behavior"], ("answer", "abstain"))
        self.assertEqual(set(cats.values()), {3})

    def test_all_configs_parse(self):
        for name in ("metrics.json", "models.json"):
            json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))
        for pid in ("A", "B", "C"):
            json.loads((ROOT / "configs" / f"params_{pid}.json").read_text(encoding="utf-8"))

    def test_models_selected_are_available(self):
        cfg = runner.load_models()
        available = set(cfg["available_at_probe"])
        for m in cfg["selected"]:
            self.assertIn(m["id"], available)


if __name__ == "__main__":
    unittest.main(verbosity=2)
