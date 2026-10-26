import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.compress import compress, detect
from stop_bullshiting.store import get_receipt, note_delete, note_get, note_list, note_put, purge, report


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name, "SBS_MIN_GAIN": "0.01"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_logs_compact_preserves_error_and_original(self):
        original = "\n".join(["2026-10-26 INFO operation complete"] * 300 +
                             ["2026-10-26 ERROR critical failure 917"] +
                             ["2026-10-26 INFO summary done"])
        reduced = compress(original, mode="logs", max_len=1300)
        self.assertIn("critical failure 917", reduced.text)
        self.assertLess(len(reduced.text), 1300)
        self.assertGreater(reduced.saved_percent, 60)
        self.assertEqual(get_receipt(reduced.receipt), original)
        self.assertEqual(report()["receipts"], 1)

    def test_small_unchanged(self):
        result = compress("status ok", mode="plain")
        self.assertEqual(result.mode, "none")
        self.assertIsNone(result.receipt)

    def test_no_store(self):
        original = "\n".join("INFO message row " + str(i) for i in range(200))
        item = compress(original, max_len=850, minimum=0, store=False)
        self.assertIsNone(item.receipt)
        self.assertIn("original not stored", item.text)

    def test_json_is_valid_when_using_json_view(self):
        original = json.dumps({"status": "ok", "total": 999, "results": [
            {"id": i, "value": "hello" * 50} for i in range(100)]})
        item = compress(original, mode="json", max_len=2200)
        self.assertTrue(item.receipt)
        self.assertEqual(get_receipt(item.receipt), original)
        self.assertIn("_sbs_omitted", item.text)

    def test_detect_modes(self):
        self.assertEqual(detect('{"hello":1}'), "json")
        self.assertEqual(detect("diff --git a/a b/a\n@@ -2 +2 @@\n"), "diff")
        self.assertEqual(detect("def test():\n    pass\n"), "code")
        self.assertEqual(detect("ERROR breaking\n"), "logs")

    def test_diff_preserves_file_and_hunk_headers(self):
        original = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1,6 +1,6 @@\n" + "\n".join(
            f"+update_{i}" for i in range(250))
        item = compress(original, mode="diff", max_len=1100)
        self.assertIn("diff --git", item.text)
        self.assertIn("@@ -1,6", item.text)
        self.assertEqual(get_receipt(item.receipt), original)

    def test_scope_notes_and_search(self):
        note_put("proj", "rules", "keep tests deterministic")
        self.assertEqual(note_get("proj", "rules"), "keep tests deterministic")
        self.assertEqual(note_list("proj", "tests")[0][0], "rules")
        self.assertEqual(note_list("other", "tests"), [])
        self.assertTrue(note_delete("proj", "rules"))
        self.assertIsNone(note_get("proj", "rules"))

    def test_purge(self):
        original = "LOG\n" * 500
        item = compress(original, max_len=600)
        self.assertTrue(item.receipt)
        self.assertEqual(purge(days=0), 0)  # strictly older, same second generally


if __name__ == "__main__":
    unittest.main()
