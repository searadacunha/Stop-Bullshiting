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
        self.assertEqual(json.loads(item.text.rsplit("\n[SBS ", 1)[0])["total"], 999)

    def test_json_keeps_middle_anomalies_and_accurate_omission_counts(self):
        records = [{"id": i, "details": "ordinary " * 80} for i in range(20)]
        records[7] = {"id": "job-" + "9" * 280, "amount": 10**70 + 123,
                      "error": "critical context " * 30, "status": "failed"}
        records[12] = {"id": 10**80 + 12, "success": False, "value": 0.125}
        original = json.dumps(records)
        item = compress(original, mode="json", max_len=2400, minimum=0)
        self.assertEqual(item.mode, "json")
        self.assertLessEqual(len(item.text), 2400)
        view = json.loads(item.text.rsplit("\n[SBS ", 1)[0])
        cursor = 0
        seen_anomalies = []
        for record in view:
            if "_sbs_omitted_items" in record:
                self.assertGreater(record["_sbs_omitted_items"], 0)
                cursor += record["_sbs_omitted_items"]
            else:
                self.assertEqual(record["id"], records[cursor]["id"])
                if cursor in (7, 12):
                    self.assertEqual(record, records[cursor])
                    seen_anomalies.append(cursor)
                cursor += 1
        self.assertEqual(cursor, len(records))
        self.assertEqual(seen_anomalies, [7, 12])
        self.assertEqual(get_receipt(item.receipt), original)

    def test_json_recognizes_failure_markers_in_middle_records(self):
        for marker in ({"errors": ["missing input"]}, {"status": " FATAL "},
                       {"success": False}, {"ok": False}, {"exit_code": 2},
                       {"failed": 1}, {"exception": {"message": "bad input"}}):
            with self.subTest(marker=marker):
                records = [{"id": i, "details": "normal " * 70} for i in range(30)]
                critical = {"id": "middle", "diagnostic": "keep all of this " * 30, **marker}
                records[15] = critical
                item = compress(json.dumps(records), max_len=2200, minimum=0, store=False)
                self.assertEqual(item.mode, "json")
                view = json.loads(item.text.rsplit("\n[SBS ", 1)[0])
                self.assertIn(critical, view)

    def test_json_retains_nested_anomalies_and_their_parent_branch(self):
        records = [{"id": i, "value": "normal " * 80} for i in range(40)]
        critical = {"request_id": "req-" + "8" * 300,
                    "result": {"detail": {"errors": [{"code": 9007199254740993,
                                                     "message": "nested failure"}]}}}
        records[19] = critical
        # payload is beyond the ordinary-key sample and beyond the usual depth
        # budget at narrower widths; its diagnostic must remain reachable.
        data = {f"noise{i}": "irrelevant " * 150 for i in range(8)}
        data["payload"] = {"batch": {"results": records}}
        original = json.dumps(data)
        item = compress(original, mode="json", max_len=2700, minimum=0)
        self.assertEqual(item.mode, "json")
        view = json.loads(item.text.rsplit("\n[SBS ", 1)[0])
        self.assertIn(critical, view["payload"]["batch"]["results"])
        self.assertEqual(get_receipt(item.receipt), original)

    def test_json_empty_error_markers_do_not_protect_normal_records(self):
        records = [{"id": i, "errors": [], "error": None, "failed": 0,
                    "success": True, "status": "ok", "value": "normal " * 80}
                   for i in range(30)]
        item = compress(json.dumps(records), max_len=1800, minimum=0, store=False)
        self.assertEqual(item.mode, "json")
        view = json.loads(item.text.rsplit("\n[SBS ", 1)[0])
        self.assertTrue(any("_sbs_omitted_items" in record for record in view))

    def test_json_bypasses_when_diagnostics_cannot_fit_without_loss(self):
        cases = [
            [{"id": i, "status": "failed", "message": "cause " * 40} for i in range(30)],
            [{"id": 1, "value": "normal " * 100},
             {"id": "critical", "error": "complete diagnostic " * 300},
             {"id": 3, "value": "normal " * 100}],
        ]
        for data in cases:
            with self.subTest(records=len(data)):
                original = json.dumps(data)
                item = compress(original, mode="json", max_len=700, minimum=0)
                self.assertEqual(item.text, original)
                self.assertEqual(item.mode, "none")
                self.assertIsNone(item.receipt)
                self.assertEqual(item.saved_percent, 0)
        self.assertEqual(report()["receipts"], 0)

    def test_json_bypasses_if_a_diagnostic_number_would_lose_precision(self):
        records = [{"id": i, "value": "normal " * 80} for i in range(30)]
        records[15] = {"status": "failed", "measurement": "precise-number"}
        for number in ("0.1234567890123456789012345", "1e999999999999999999999999"):
            with self.subTest(number=number):
                original = json.dumps(records).replace('"precise-number"', number)
                item = compress(original, mode="json", max_len=1800, minimum=0)
                self.assertEqual(item.text, original)
                self.assertEqual(item.mode, "none")
                self.assertIsNone(item.receipt)

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

    def test_auto_preserves_code_diff_and_csv_with_explicit_lossy_opt_in(self):
        cases = {
            "code": "\n".join(f"def helper_{i}():\n    return {i}" for i in range(100)),
            "diff": "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1,6 +1,6 @@\n" +
                    "\n".join(f"+update_{i}" for i in range(250)),
            "csv": "id,value\n" + "\n".join(f"{i}," + "data" * 25 for i in range(60)),
        }
        for mode, original in cases.items():
            with self.subTest(mode=mode):
                self.assertEqual(detect(original), mode)
                automatic = compress(original, max_len=900, minimum=0, store=False)
                self.assertEqual(automatic.text, original)
                self.assertEqual(automatic.mode, "none")
                self.assertIsNone(automatic.receipt)
                explicit = compress(original, mode=mode, max_len=900, minimum=0, store=False)
                self.assertEqual(explicit.mode, mode)
                self.assertNotEqual(explicit.text, original)
                self.assertLessEqual(len(explicit.text), 900)

    def test_auto_preserves_indented_python_and_exported_javascript(self):
        snippets = [
            "\n".join(f"    def helper_{i}(self):\n        return {i}" for i in range(80)),
            "export function check(amount) {\n" +
            "\n".join(f"  const value{i} = {i};" for i in range(80)) +
            "\n  return amount > 0;\n}\n",
        ]
        for original in snippets:
            with self.subTest(prefix=original[:40]):
                self.assertEqual(detect(original), "code")
                item = compress(original, max_len=700, minimum=0, store=False)
                self.assertEqual(item.text, original)
                self.assertEqual(item.mode, "none")
                self.assertIsNone(item.receipt)

    def test_code_declarations_preserve_existing_format_priority(self):
        declarations = [
            "  async function load() {", "export default function render() {",
            "export default async function load() {", "function* values() {",
            "const answer = 42;", "  let answer: number = 42;", "var count = 0;",
            "export const handler = async () => {};", "export interface Result<T> {",
            "export default class Reader {", "export abstract class Reader {",
            "    from .module import helper",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                self.assertEqual(detect(declaration), "code")
        self.assertEqual(detect(json.dumps({"source": declarations})), "json")
        self.assertEqual(detect("diff --git a/x b/x\nexport function check() {"), "diff")
        self.assertEqual(detect("INFO build output\nexport function check() {"), "logs")
        for prose in ("let us compare these values", "const is a declaration keyword", "functionality() matters"):
            self.assertEqual(detect(prose), "plain")

    def test_python_traceback_in_middle_is_preserved_verbatim(self):
        traceback = ('Traceback (most recent call last):\n'
                     '  File "worker.py", line 42, in process\n'
                     '    run_job(job_id)\n'
                     'ValueError: invalid job 12345678901234567890')
        original = "\n".join(["INFO completed normal operation"] * 120 +
                             [traceback] + ["INFO completed other operation"] * 120)
        for mode in ("auto", "logs", "plain"):
            with self.subTest(mode=mode):
                item = compress(original, mode=mode, max_len=900, minimum=0)
                self.assertEqual(item.text, original)
                self.assertEqual(item.mode, "none")
                self.assertIsNone(item.receipt)

    def test_log_diagnostic_lines_are_complete_or_output_is_unchanged(self):
        diagnostic = "ERROR exact diagnostic " + "context-" * 120
        original = "\n".join([f"INFO normal operation {i}" for i in range(120)] +
                             [diagnostic] + [f"INFO normal operation {i}" for i in range(120, 240)])
        for mode in ("auto", "logs", "plain"):
            with self.subTest(mode=mode):
                compact = compress(original, mode=mode, max_len=1600, minimum=0, store=False)
                self.assertNotEqual(compact.mode, "none")
                self.assertIn(diagnostic, compact.text.splitlines())
                self.assertLessEqual(len(compact.text), 1600)
                tight = compress(original, mode=mode, max_len=700, minimum=0)
                self.assertEqual(tight.text, original)
                self.assertEqual(tight.mode, "none")
                self.assertIsNone(tight.receipt)

    def test_many_log_diagnostics_cannot_fall_back_to_head_and_tail(self):
        original = "\n".join(f"INFO ordinary operation {i}\nERROR diagnostic {i}: " + "details " * 15
                             for i in range(40))
        item = compress(original, mode="logs", max_len=900, minimum=0)
        self.assertEqual(item.text, original)
        self.assertEqual(item.mode, "none")
        self.assertIsNone(item.receipt)

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
