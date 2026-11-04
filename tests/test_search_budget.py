"""Regression checks for exact, match-centered receipt search under budgets."""

import os
import tempfile
import unittest
from unittest.mock import patch

from stop_bullshiting.search import find, render
from stop_bullshiting.store import get_receipt, save_receipt


class SearchBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def save(self, text, source="test"):
        return save_receipt(text, source=source, mode="plain", visible_chars=1)

    def assertExact(self, hit, original):
        lines = original.split("\n")
        expected = "\n".join(f"{line}: {lines[line - 1]}" for line in range(hit["start"], hit["end"] + 1))
        self.assertEqual(hit["text"], expected)

    def test_long_context_cannot_hide_the_matching_line(self):
        original = "x" * 2000 + "\nE_TARGET_77 exact evidence\n" + "y" * 2000
        identifier = self.save(original)
        for use_fts in (True, False):
            with self.subTest(use_fts=use_fts):
                result = find("E_TARGET_77", max_chars=256, use_fts=use_fts)
                self.assertEqual(len(result["hits"]), 1)
                hit = result["hits"][0]
                self.assertEqual(hit["receipt"], identifier)
                self.assertEqual((hit["start"], hit["end"]), (2, 2))
                self.assertEqual(hit["text"], "2: E_TARGET_77 exact evidence")
                self.assertLessEqual(len(render(result)), 256)
                self.assertExact(hit, original)
        self.assertEqual(get_receipt(identifier), original)

    def test_ranges_and_line_endings_remain_exact_after_selection(self):
        original = "\r\n".join(["padding"] * 10 + ["x" * 2000, "TARGET at boundary  \t", "after"])
        self.save(original)
        for use_fts in (True, False):
            result = find("TARGET", max_chars=256, use_fts=use_fts)
            self.assertEqual(result["hits"][0]["start"], 12)
            self.assertIn("TARGET at boundary  \t\r", result["hits"][0]["text"])
            for hit in result["hits"]:
                self.assertExact(hit, original)

    def test_global_budget_counts_headers_and_notices_for_all_hits(self):
        for index in range(8):
            self.save(f"TARGET evidence {index}\n" + "nearby context\n" * 10, source="command " + "s" * 90)
        for use_fts in (True, False):
            for budget in (256, 512, 800, 6000):
                with self.subTest(use_fts=use_fts, budget=budget):
                    result = find("TARGET", limit=8, receipts=8, max_chars=budget, use_fts=use_fts)
                    self.assertLessEqual(len(render(result)), budget)
                    self.assertEqual(len(result["hits"]) + len(result["omitted_passages"]), 8)
                    for hit in result["hits"]:
                        self.assertIn("TARGET", hit["text"])
                        self.assertExact(hit, get_receipt(hit["receipt"]))

    def test_oversized_match_is_explicit_and_original_is_recoverable(self):
        original = "TARGET " + "x" * 2000
        identifier = self.save(original)
        for use_fts in (True, False):
            result = find("TARGET", max_chars=256, use_fts=use_fts)
            self.assertEqual(result["hits"], [])
            self.assertEqual(result["omitted_passages"], [{"receipt": identifier, "start": 1, "end": 1}])
            output = render(result)
            self.assertIn("matching passages omitted", output)
            self.assertIn(f"sbs show {identifier}", output)
            self.assertNotIn("no match", output)
            self.assertLessEqual(len(output), 256)
        self.assertEqual(get_receipt(identifier), original)

    def test_oversized_match_does_not_hide_a_later_short_result(self):
        short = self.save("TARGET actionable evidence")
        self.save("TARGET " * 2000)
        for use_fts in (True, False):
            result = find("TARGET", limit=2, max_chars=512, use_fts=use_fts)
            self.assertEqual([hit["receipt"] for hit in result["hits"]], [short])
            self.assertEqual(len(result["omitted_passages"]), 1)
            self.assertLessEqual(len(render(result)), 512)

    def test_multiple_matching_lines_beat_unrelated_context(self):
        original = "TARGET first\n" + "x" * 2000 + "\nTARGET second\nTARGET third"
        self.save(original)
        for use_fts in (True, False):
            result = find("TARGET", max_chars=256, use_fts=use_fts)
            self.assertEqual((result["hits"][0]["start"], result["hits"][0]["end"]), (3, 4))
            self.assertExact(result["hits"][0], original)

    def test_new_oversized_receipt_does_not_hide_older_evidence(self):
        original = "TARGET old evidence"
        identifier = self.save(original)
        self.save("new noise " * 1000)
        result = find("TARGET", max_bytes=100, max_chars=256)
        self.assertEqual(result["hits"][0]["receipt"], identifier)
        self.assertEqual(result["coverage"], {"considered_receipts": 2, "searched_receipts": 1,
                                             "searched_bytes": len(original.encode("utf-8")),
                                             "skipped_receipts": 1, "receipt_limit": 200, "byte_limit": 100})
        self.assertIn("1/2 receipts skipped", render(result))
        self.assertLessEqual(len(render(result)), 256)

    def test_byte_budget_counts_utf8_bytes_and_preserves_capacity_after_skip(self):
        accepted = "TARGET été 日本語"
        identifier = self.save(accepted)
        self.save("é" * 12)  # 24 UTF-8 bytes, although only 12 characters.
        budget = len(accepted.encode("utf-8"))
        self.assertLess(budget, 24)
        result = find("TARGET", max_bytes=budget)
        self.assertEqual(result["hits"][0]["receipt"], identifier)
        self.assertEqual(result["coverage"]["searched_bytes"], budget)
        self.assertEqual(result["coverage"]["skipped_receipts"], 1)

    def test_byte_budget_is_cumulative_and_reports_incomplete_coverage(self):
        self.save("TARGET missing")
        newest = self.save("newest text")
        budget = len("newest text".encode("utf-8"))
        result = find("TARGET", max_bytes=budget)
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["coverage"]["searched_receipts"], 1)
        self.assertEqual(result["coverage"]["searched_bytes"], budget)
        self.assertEqual(result["coverage"]["skipped_receipts"], 1)
        self.assertIn("searched passages", render(result))
        self.assertIn("receipts skipped", render(result))
        self.assertEqual(get_receipt(newest), "newest text")

    def test_recent_scope_is_visible_when_older_matches_are_outside_it(self):
        self.save("TARGET old evidence")
        self.save("newest unrelated output")
        for use_fts in (True, False):
            result = find("TARGET", receipts=1, max_chars=256, use_fts=use_fts)
            self.assertEqual(result["hits"], [])
            self.assertEqual(result["coverage"]["considered_receipts"], 1)
            self.assertIn("scope: most recent 1 receipt", render(result))
            self.assertLessEqual(len(render(result)), 256)

    def test_all_notices_share_the_budget_when_no_match_line_fits(self):
        self.save("TARGET " + "x" * 2000)
        self.save("noise" * 1000)
        for use_fts in (True, False):
            result = find("TARGET", receipts=2, max_bytes=2100, max_chars=256, use_fts=use_fts)
            output = render(result)
            self.assertEqual(result["hits"], [])
            self.assertIn("matching passages omitted", output)
            self.assertIn("scope: most recent 2 receipts", output)
            self.assertIn("1/2 receipts skipped", output)
            self.assertLessEqual(len(output), 256)

    def test_fts_snippet_uses_fts_matching_rules_for_diacritics(self):
        original = "x" * 2000 + "\nCAFÉ evidence"
        self.save(original)
        result = find("cafe", max_chars=256)
        if result["engine"] != "fts5":
            self.skipTest("SQLite build has no FTS5")
        self.assertEqual(result["hits"][0]["text"], "2: CAFÉ evidence")
        self.assertExact(result["hits"][0], original)

    def test_highlight_markers_in_source_cannot_corrupt_exact_lines(self):
        original = "x" * 2000 + "\n\ufdd0\ufdd0 TARGET \ufdd1\ufdd1 evidence"
        self.save(original)
        result = find("TARGET", max_chars=256)
        self.assertIn("TARGET", result["hits"][0]["text"])
        self.assertExact(result["hits"][0], original)

    def test_lone_surrogates_use_python_ranking_and_restore_exactly(self):
        original = "\ud800\nTARGET intact"
        identifier = self.save(original)
        result = find("TARGET", max_bytes=len(original.encode("utf-8", "surrogatepass")))
        self.assertEqual(result["engine"], "python")
        self.assertEqual(result["hits"][0]["receipt"], identifier)
        self.assertExact(result["hits"][0], original)

    def test_order_is_deterministic_for_equal_ranked_passages(self):
        self.save("TARGET same")
        newest = self.save("TARGET same")
        for use_fts in (True, False):
            first = find("TARGET", use_fts=use_fts)
            second = find("TARGET", use_fts=use_fts)
            self.assertEqual(first, second)
            self.assertEqual(first["hits"][0]["receipt"], newest)

    def test_invalid_limits_fail_before_searching(self):
        for key, bad in (("limit", 0), ("receipts", 0), ("receipts", True),
                         ("max_bytes", 0), ("max_bytes", 1.5), ("max_chars", 255),
                         ("max_chars", True)):
            with self.subTest(key=key, bad=bad):
                with patch("stop_bullshiting.search.iter_receipts") as iterator:
                    with self.assertRaisesRegex(ValueError, key):
                        find("TARGET", **{key: bad})
                    iterator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
