import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from stop_bullshiting.cli import main
from stop_bullshiting.metrics import measure_reference_tokens
from stop_bullshiting.store import save_receipt


class TokenMetricTests(unittest.TestCase):
    def test_named_counts_keep_negative_savings_and_do_not_claim_billing(self):
        calls = []

        def encode(text, *, disallowed_special):
            calls.append((text, disallowed_special))
            return [0] * ({"source": 2, "<|endoftext|>": 3}[text])

        tokenizer = SimpleNamespace(__version__="test-double", get_encoding=lambda name:
                                    SimpleNamespace(encode=encode))
        with patch.dict("sys.modules", {"tiktoken": tokenizer}):
            result = measure_reference_tokens("source", "<|endoftext|>", "o200k_base")
        self.assertEqual(result["saved"], -1)
        self.assertEqual(result["reduction_pct"], -50.0)
        self.assertFalse(result["provider_billing_measured"])
        self.assertEqual(calls, [("source", ()), ("<|endoftext|>", ())])

    def test_missing_optional_dependency_is_an_explicit_error(self):
        with patch.dict("sys.modules", {"tiktoken": None}):
            with self.assertRaisesRegex(ValueError, r"\[tokens\]"):
                measure_reference_tokens("before", "after", "o200k_base")

    def test_unknown_encoding_is_not_treated_as_a_model_name(self):
        with self.assertRaisesRegex(ValueError, "reference tokenizer"):
            measure_reference_tokens("before", "after", "claude")


class MeasurementCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def call(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_default_benchmark_remains_dependency_free(self):
        with patch.dict("sys.modules", {"tiktoken": None}):
            status, stdout, stderr = self.call(["bench"])
        self.assertEqual(status, 0, stderr)
        report = json.loads(stdout)
        self.assertFalse(report["model_tokens_measured"])
        self.assertNotIn("reference_token_counts", report)
        self.assertTrue(report["preview_marker_included"])
        self.assertFalse(report["wrapper_diagnostics_included"])
        self.assertFalse(report["receipt_stored"])

    def test_requested_counter_never_silently_falls_back_to_characters(self):
        with patch.dict("sys.modules", {"tiktoken": None}):
            status, stdout, stderr = self.call(["bench", "--tokenizer", "o200k_base"])
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("[tokens]", stderr)

    def test_counter_receives_actual_preview_including_marker(self):
        captured = []

        def measure(original, visible, name):
            captured.append((original, visible, name))
            return {"encoding": name, "provider_billing_measured": False}

        with patch("stop_bullshiting.cli.measure_reference_tokens", side_effect=measure):
            status, stdout, stderr = self.call(["bench", "--tokenizer", "cl100k_base"])
        self.assertEqual(status, 0, stderr)
        report = json.loads(stdout)
        self.assertFalse(report["model_tokens_measured"])
        self.assertEqual(report["reference_token_counts"]["encoding"], "cl100k_base")
        self.assertIn("original not stored", captured[0][1])
        self.assertEqual(len(captured[0][1]), report["visible_chars"])

    def test_show_preserves_full_original_and_offers_targeted_recovery(self):
        original = "first\n  ERROR café account-17 amount=12.50  \nthird\nfourth\n"
        receipt = save_receipt(original, source="test", mode="logs", visible_chars=10)
        self.assertEqual(self.call(["show", receipt]), (0, original, ""))
        status, excerpt, stderr = self.call(["show", receipt, "--find", "account-17",
                                            "--context", "0"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("  ERROR café account-17 amount=12.50  ", excerpt)
        self.assertNotIn("first", excerpt)
        self.assertNotIn("fourth", excerpt)
        status, excerpt, stderr = self.call(["show", receipt, "--start", "3", "--end", "3"])
        self.assertEqual(status, 0, stderr)
        self.assertIn("third", excerpt)
        self.assertNotIn("account-17", excerpt)


if __name__ == "__main__":
    unittest.main()
