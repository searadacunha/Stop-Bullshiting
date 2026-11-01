import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.cli import main
from stop_bullshiting.mcp import invoke
from stop_bullshiting.search import find, render
from stop_bullshiting.store import save_receipt


class ReceiptSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.env.start()
        noise = "\n".join(f"row {i} nothing to see" for i in range(300))
        self.first = save_receipt(noise + "\nE_BALANCE_77 ledger mismatch for account 9\n" + noise,
                                  source="run:a", mode="logs", visible_chars=1)
        self.second = save_receipt("\n".join(f"other {i} ledger line" for i in range(400)),
                                   source="run:b", mode="logs", visible_chars=1)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_finds_exact_passage_with_its_original_line_numbers(self):
        result = find("E_BALANCE_77 mismatch")
        hit = result["hits"][0]
        self.assertEqual(hit["receipt"], self.first)
        self.assertIn("301: E_BALANCE_77 ledger mismatch for account 9", hit["text"])
        self.assertLess(len(hit["text"]), 1000)

    def test_both_engines_agree_on_the_best_receipt(self):
        for query in ("E_BALANCE_77", "mismatch account", "account nosuchword"):
            fts = find(query)
            plain = find(query, use_fts=False)
            self.assertEqual(plain["engine"], "python")
            self.assertEqual(fts["hits"][0]["receipt"], plain["hits"][0]["receipt"], query)
            self.assertEqual(fts["hits"][0]["start"], plain["hits"][0]["start"], query)

    def test_missing_fts5_falls_back(self):
        with patch("stop_bullshiting.search._fts_rank", return_value=None):
            self.assertEqual(find("E_BALANCE_77")["engine"], "python")

    def test_unicode_query(self):
        identifier = save_receipt("préfixe\nfichier été 日本語\nfin", source="u", mode="plain", visible_chars=1)
        self.assertEqual(find("été")["hits"][0]["receipt"], identifier)
        self.assertEqual(find("été", use_fts=False)["hits"][0]["receipt"], identifier)

    def test_no_plaintext_copy_reaches_the_disk(self):
        find("E_BALANCE_77")
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), ["store.sqlite3"])
        raw = (Path(self.temp.name) / "store.sqlite3").read_bytes()
        self.assertNotIn(b"E_BALANCE_77", raw)
        conn = sqlite3.connect(Path(self.temp.name) / "store.sqlite3")
        self.addCleanup(conn.close)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertEqual({row[0] for row in tables}, {"receipts", "notes", "counters"})

    def test_output_is_bounded_and_no_match_is_explicit(self):
        result = find("ledger", limit=20, max_chars=800)
        self.assertLessEqual(sum(len(hit["text"]) for hit in result["hits"]), 800)
        self.assertIn("no match", render(find("zzzzzz")))
        for bad in ("", "  ", "!!!"):
            with self.assertRaises(ValueError):
                find(bad)
        with self.assertRaises(ValueError):
            find("x", limit=0)

    def test_cli_and_mcp(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["find", "E_BALANCE_77", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["hits"][0]["receipt"], self.first)
        self.assertIn("E_BALANCE_77", invoke("sbs_find", {"query": "E_BALANCE_77"})["content"][0]["text"])
        self.assertTrue(invoke("sbs_find", {"query": "x", "limit": 99})["isError"])

    def test_reading_back_is_counted(self):
        main_out = io.StringIO()
        with contextlib.redirect_stdout(main_out):
            main(["show", self.first])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["stats"])
        self.assertIn("Originals read back: 1 restores", out.getvalue())


if __name__ == "__main__":
    unittest.main()
