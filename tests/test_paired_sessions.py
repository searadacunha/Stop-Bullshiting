"""Paired task results must not manufacture missing token or cost measurements."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/paired_sessions.py"
spec = importlib.util.spec_from_file_location("paired_sessions", MODULE_PATH)
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)


def trial(task, repeat, variant, success, **extra):
    return {"task_id": task, "repeat": repeat, "variant": variant,
            "success": success, "model": "local-test-only", "task_revision": "task-v1",
            "repository_sha": "snapshot123", **extra}


class PairedSessionsTests(unittest.TestCase):
    def test_paired_success_and_missing_cost_are_explicit(self):
        rows = [
            trial("repair-import", 0, "control", True, tokens_total=100, tool_calls=7),
            trial("repair-import", 0, "sbs", True, tokens_total=65, tool_calls=8,
                  restore_calls=2),
            trial("repair-import", 1, "control", False, tokens_total=110),
            trial("repair-import", 1, "sbs", True, tokens_total=90),
            trial("not-finished", 0, "control", True, tokens_total=4),
        ]
        result = paired.summarize(rows)
        self.assertEqual(result["complete_pairs"], 2)
        self.assertEqual(result["success"], {"control": 1, "sbs": 2, "n_each": 2})
        self.assertEqual(result["metrics"]["tokens_total"]["mean_delta_sbs_minus_control"], -27.5)
        self.assertEqual(result["metrics"]["cost_usd"]["pairs_measured"], 0)
        self.assertIsNone(result["metrics"]["cost_usd"]["mean_delta_sbs_minus_control"])
        self.assertEqual(result["metrics"]["restore_calls"]["pairs_measured"], 0)
        self.assertEqual(len(result["unmatched_trials"]), 1)

    def test_duplicate_pairs_or_mismatched_models_are_refused(self):
        row = trial("x", 0, "control", True)
        with self.assertRaisesRegex(ValueError, "Duplicate trial"):
            paired.summarize([row, row, trial("x", 0, "sbs", True)])
        with self.assertRaisesRegex(ValueError, "Non-equivalent pair"):
            paired.summarize([row, trial("x", 0, "sbs", True, model="other")])

    def test_jsonl_parsing_validates_missing_and_invalid_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.jsonl"
            path.write_text(json.dumps(trial("a", 0, "control", True,
                                             cost_usd=0.004)) + "\n", encoding="utf-8")
            self.assertEqual(paired.read_trials(path)[0]["cost_usd"], 0.004)
            for invalid in [trial("a", 0, "control", True, tool_calls=True),
                            trial("a", 0, "control", True, tokens_total=-2),
                            {**trial("a", 0, "control", True), "success": 1}]:
                path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    paired.read_trials(path)


if __name__ == "__main__":
    unittest.main()
