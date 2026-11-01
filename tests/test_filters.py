import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.compress import compress
from stop_bullshiting.filters import (classify, clean, git_diff_view, git_log_view, git_status_view,
                                      minify_json, producer, search_view, tests_view)
from stop_bullshiting.store import get_receipt, purge, report, save_receipt


class ClassifyTests(unittest.TestCase):
    def test_known_commands(self):
        cases = {"git status": "git-status", "git -C repo --no-pager log -5": "git-log", "git diff HEAD~1": "git-diff",
                 "git show abc": "git-diff", "git grep foo": "search", "rg foo src": "search", "grep -rn foo .": "search",
                 "pytest -v": "tests", "python3 -m pytest": "tests", "python -m unittest discover": "tests",
                 ".venv/bin/python -m pytest -q": "tests", "npm test": "tests", "npm run test:unit": "tests",
                 "pnpm test": "tests", "npx jest": "tests", "go test ./...": "tests", "cargo test": "tests",
                 "rtk git log": "git-log", "cd repo && FOO=1 pytest": "tests", "uv run pytest": "tests",
                 "rg foo | head -50": "search", "pytest 2>&1 | tail -20": "tests", "git log -3 | head": "git-log"}
        for command, expected in cases.items():
            self.assertEqual(classify(command), expected, command)

    def test_anything_changing_stdout_is_not_classified(self):
        for command in ("git log | wc -l", "rg foo | sort", "cat a; rg foo", "pytest && git status", "git log > out",
                        "$(git log)", "`git log`", "git push", "npm install", "python3 x.py", "echo 'git log'",
                        "tail -f log | head", "git status | head -c 10", "", "git", "unterminated 'quote",
                        "for x in a; do rg $x; done", "rg foo\nrm x"):
            self.assertIsNone(classify(command), command)
        self.assertIsNone(producer(None))


class ViewTests(unittest.TestCase):
    def test_pytest_keeps_everything_but_passing_rows(self):
        text = "\n".join(["===== test session starts =====", "collected 3 items",
                          "tests/a.py::test_one PASSED                [ 33%]",
                          "tests/a.py::test_two SKIPPED (no db)       [ 66%]",
                          "tests/a.py::test_three XFAIL               [100%]",
                          "===== warnings summary =====", "tests/a.py:3: DeprecationWarning: old api",
                          "===== 1 passed, 1 skipped, 1 xfailed, 1 warning in 0.12s ====="])
        view = tests_view(text)
        self.assertNotIn("test_one", view)
        for kept in ("test_two SKIPPED (no db)", "test_three XFAIL", "DeprecationWarning: old api", "1 xfailed"):
            self.assertIn(kept, view)

    def test_unittest_jest_go_and_cargo_rows(self):
        unit = "test_a (m.T) ... ok\ntest_b (m.T) ... skipped 'later'\n\nRan 2 tests in 0.1s\n\nOK (skipped=1)"
        self.assertEqual(tests_view(unit).split("\n")[0], "test_b (m.T) ... skipped 'later'")
        jest = "PASS src/a.test.js\n  ✓ works (3 ms)\n  ○ skipped later\nTests:       1 skipped, 1 passed, 2 total"
        self.assertIn("○ skipped later", tests_view(jest))
        self.assertNotIn("works", tests_view(jest))
        golang = "=== RUN   TestA\n--- PASS: TestA (0.00s)\nPASS\nok  \tpkg/a\t0.1s"
        self.assertEqual(tests_view(golang).split("\n")[0], "ok  \tpkg/a\t0.1s")
        cargo = "test a::b ... ok\ntest a::c ... ignored\n\ntest result: ok. 1 passed; 0 failed; 1 ignored"
        self.assertIn("ignored", tests_view(cargo).split("\n")[0])

    def test_output_without_a_test_summary_is_not_touched(self):
        self.assertIsNone(tests_view("starting ... ok\nanother ... ok\n"))

    def test_git_status_keeps_tracked_paths_and_state(self):
        untracked = "".join(f"\tdata/raw/file_{i}.csv\n" for i in range(50))
        text = ('On branch main\nYour branch is ahead of \'origin/main\' by 2 commits.\n  (use "git push" to publish)\n\n'
                'Changes not staged for commit:\n  (use "git add <file>..." to update)\n\tmodified:   src/core.py\n\n'
                f'Untracked files:\n  (use "git add <file>..." to include)\n{untracked}\n'
                'no changes added to commit (use "git add" and/or "git commit -a")\n')
        view = git_status_view(text)
        for kept in ("On branch main", "ahead of 'origin/main' by 2", "modified:   src/core.py", "no changes added",
                     "data/raw/ (50 untracked)"):
            self.assertIn(kept, view)
        self.assertNotIn('(use "git add <file>', view)
        short = " M src/core.py\n" + "".join(f"?? x/y/{i}\n" for i in range(40))
        self.assertIn(" M src/core.py", git_status_view(short))
        self.assertIn("x/y/ (40 untracked)", git_status_view(short))

    def test_git_log_keeps_every_commit_identity(self):
        sha = "a" * 40
        text = (f"commit {sha} (HEAD -> main)\nMerge: 111 222\nAuthor: A B <a@b.c>\nDate:   Mon Oct 26 12:00:00 2026 +0000\n\n"
                f"    Fix overflow in ledger\n\n    Long body line\n    Signed-off-by: A\n\ncommit {'b' * 40}\n"
                "Author: C <c@d.e>\nDate:   Sun Oct 25 12:00:00 2026 +0000\n\n    Second\n")
        view = git_log_view(text)
        rows = view.split("\n")
        self.assertEqual(len(rows), 2)
        for kept in (sha, "(HEAD -> main)", "A B <a@b.c>", "Mon Oct 26", "Fix overflow in ledger", "[+2 body lines]"):
            self.assertIn(kept, rows[0])
        self.assertIn("b" * 40, rows[1])

    def test_git_log_with_patch_or_custom_format_is_not_touched(self):
        patched = f"commit {'a' * 40}\nAuthor: A\nDate: x\n\n    Msg\n\ndiff --git a/x b/x\n+y\n"
        self.assertIsNone(git_log_view(patched))
        self.assertIsNone(git_log_view("abc1234 one line\nabc1235 another\n"))

    def test_long_git_log_fits_budget_and_says_what_is_gone(self):
        text = "".join(f"commit {i:040x}\nAuthor: A\nDate: d\n\n    subject {i}\n\n" for i in range(500))
        view = git_log_view(text, 2000)
        self.assertLess(len(view), 2000)
        self.assertIn("entries NOT shown", view)
        self.assertIn("subject 0", view)
        self.assertIn("subject 499", view)

    def test_diff_folds_only_generated_files(self):
        source = "diff --git a/src/a.py b/src/a.py\nindex 1..2 100644\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        lock = ("diff --git a/package-lock.json b/package-lock.json\nindex 3..4 100644\n--- a/package-lock.json\n"
                "+++ b/package-lock.json\n@@ -1,3 +1,3 @@\n-a\n+b\n c\n")
        view = git_diff_view(source + lock + source.replace("a.py", "b.py"))
        self.assertIn("-old\n+new", view)
        self.assertIn("src/b.py", view)
        self.assertIn("diff --git a/package-lock.json", view)
        self.assertIn("generated file, 4 lines omitted, +1 -1", view)
        self.assertNotIn("\n+b\n", view)
        self.assertIsNone(git_diff_view(source))

    def test_diff_fold_stops_at_the_next_commit_or_file(self):
        lock = ("diff --git a/yarn.lock b/yarn.lock\nindex 3..4 100644\n--- a/yarn.lock\n+++ b/yarn.lock\n"
                "@@ -1,2 +1,2 @@\n-a\n+b\n c\n")
        following = ("\ncommit bbb\nAuthor: A\n\n    second: fixes ERROR 500\n\n"
                     "diff --git a/src/x.py b/src/x.py\n@@ -1 +1 @@\n-old\n+new\n")
        view = git_diff_view(lock + following)
        for kept in ("commit bbb", "second: fixes ERROR 500", "diff --git a/src/x.py", "+new"):
            self.assertIn(kept, view)
        self.assertIn("generated file, 4 lines omitted, +1 -1", view)
        combined = lock + "diff --cc foo.py\nindex 1,2..3\n--- a/foo.py\n+++ b/foo.py\n@@@ -1,1 -1,1 +1,1 @@@\n++z\n"
        view = git_diff_view(combined)
        self.assertIn("diff --cc foo.py", view)
        self.assertIn("++z", view)

    def test_git_show_of_a_file_is_not_a_patch(self):
        self.assertIsNone(classify("git show HEAD:docs/change.patch"))
        self.assertEqual(classify("git show HEAD"), "git-diff")

    def test_failing_run_behind_a_pipe_keeps_every_line(self):
        for text in ("t.py::a PASSED\nt.py::b FAILED\n===== 1 failed, 1 passed in 1s =====",
                     "test a ... ok\ntest b ... FAILED\n\ntest result: FAILED. 1 passed; 1 failed",
                     "PASS a.js\n  ✓ one\nFAIL b.js\n  ✕ two\nTests:       1 failed, 1 passed, 2 total",
                     "=== RUN   TestA\n--- FAIL: TestA (0.00s)\nFAIL\nok  \tpkg\t0.1s"):
            self.assertIsNone(tests_view(text), text)
        self.assertIsNotNone(tests_view("test_failed_login (m.T) ... ok\n\nRan 1 test in 0.1s\n\nOK"))

    def test_search_grouping_is_lossless_until_the_budget(self):
        text = "\n".join(f"pkg/mod_{i % 2}.py:{i}:hit {i}" for i in range(40))
        view = search_view(text)
        for i in range(40):
            self.assertIn(f"{i}:hit {i}", view)
        self.assertEqual(view.count("pkg/mod_0.py"), 1)
        small = search_view(text, 200)
        self.assertLessEqual(len(small), 200)
        self.assertIn("pkg/mod_1.py (20 matches)", small)
        self.assertIn("more in this file", small)

    def test_search_leaves_other_shapes_alone(self):
        for text in ("plain line\nanother\n", "a.py-3-context\na.py:4:match\n", "a.py:1:x\nb.py:2:y\nc.py:3:z\n"):
            self.assertIsNone(search_view(text), text)

    def test_clean_removes_only_invisible_noise(self):
        self.assertEqual(clean("\x1b[31merror\x1b[0m: bad\n"), "error: bad\n")
        self.assertEqual(clean("10%\r50%\r100%\ndone\r\n"), "100%\ndone\r\n")
        self.assertEqual(clean("a\na\nb\n"), "a\na\nb\n")
        self.assertEqual(clean("a\na\na\na\nb"), "a\n... [previous line repeated 3 more times]\nb")
        self.assertEqual(clean("x\n\n\n\ny"), "x\n\n\n\ny")

    def test_minify_keeps_values_order_and_number_spelling(self):
        text = '{\n  "b": 1.10,\n  "a": "keep  spaces\\"  \\n",\n  "c": [ 1, 2 ],\n  "é": 12345678901234567890\n}\n'
        packed = minify_json(text)
        self.assertEqual(packed, '{"b":1.10,"a":"keep  spaces\\"  \\n","c":[1,2],"é":12345678901234567890}')
        self.assertEqual(json.loads(packed), json.loads(text))
        self.assertEqual(minify_json("not json {"), "not json {")
        self.assertEqual(minify_json("[1,2"), "[1,2")


class CompressWithCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_view_restores_exactly_including_unicode_and_crlf(self):
        text = "".join(f"src/ünï/çødé_{i % 3}.py:{i}:日本語 {i}\r\n" for i in range(300))
        item = compress(text, command="rg foo")
        self.assertEqual(item.mode, "search")
        self.assertEqual(get_receipt(item.receipt), text)
        self.assertIn(f"sbs show {item.receipt}", item.text)

    def test_marker_is_never_cut_from_a_large_view(self):
        text = "".join(f"tests/t.py::test_{i} SKIPPED (reason {i})\n" for i in range(800)) + "".join(
            f"tests/t.py::p_{i} PASSED\n" for i in range(800)) + "===== 800 passed, 800 skipped in 1s =====\n"
        item = compress(text, command="pytest -v", max_len=500)
        self.assertGreater(len(item.text), 500)
        self.assertIn(f"sbs show {item.receipt}", item.text)
        self.assertIn("SKIPPED (reason 799)", item.text)

    def test_view_without_gain_keeps_original_and_stores_nothing(self):
        text = "\n".join(f"pkg/a_{i}.py:{i}:{'x' * 40}" for i in range(100))
        item = compress(text, command="git log", generic=False)
        self.assertEqual((item.text, item.receipt, item.mode), (text, None, "none"))
        self.assertEqual(report()["receipts"], 0)

    def test_secrets_bypass_every_view(self):
        text = "".join(f"a.py:{i}:password=abcdefghijklmnopqrstuvwxyz\n" for i in range(300))
        item = compress(text, command="rg password")
        self.assertEqual(item.text, text)
        self.assertEqual(report()["receipts"], 0)

    def test_common_credential_shapes_are_not_stored(self):
        filler = "".join(f"a.py:{i}:ordinary line\n" for i in range(400))
        for secret in ("GITHUB_TOKEN=abcdefgh12345678", "AWS_SECRET_ACCESS_KEY=abcd1234efgh5678",
                       "DATABASE_URL=postgres://user:hunter22@db.example.com/app", "password=Abc!defghijklmnop",
                       "\x1b[1mpassword\x1b[0m=abcdefghijklmnop", "ghp_abcdefghijklmnopqrstuvwx", "sk-abcdefghijklmnopqrstuvwx",
                       "AKIAABCDEFGHIJKLMNOP", '"client_secret": "abcdefgh12345678"'):
            item = compress(filler + secret + "\n", mode="plain")
            self.assertEqual((item.mode, item.receipt), ("none", None), secret)
        self.assertEqual(report()["receipts"], 0)

    def test_ordinary_counters_and_urls_are_not_mistaken_for_secrets(self):
        filler = "".join(f"row {i} plain value\n" for i in range(600))
        for text in ('"input_tokens": 123456789\n', "see https://example.com/docs/page\n", "token: abc\n", "tokens=abcdefghijkl\n"):
            self.assertIsNotNone(compress(filler + text, mode="plain").receipt, text)

    def test_common_error_lines_survive_a_generic_preview(self):
        for line in ("TypeError: Cannot read property x", "FAIL src/a.test.js", "npm ERR! code ELIFECYCLE",
                     "Segmentation fault (core dumped)", "Killed", "process exited with status 2"):
            text = "\n".join(["ordinary progress line"] * 300 + [line] + ["ordinary progress line"] * 300)
            item = compress(text, mode="plain")
            self.assertIn(line, item.text, line)

    def test_lone_surrogate_restores_exactly(self):
        text = "\n".join(["row \ud800 of a listing with enough text"] * 400)
        item = compress(text, mode="plain")
        self.assertEqual(get_receipt(item.receipt), text)

    def test_a_view_keeps_every_commit_and_match_within_the_item_budget(self):
        log = "".join(f"commit {i:040x}\nAuthor: A <a@b.c>\nDate: d\n\n    subject number {i}\n\n    body {i}\n\n"
                      for i in range(150))
        item = compress(log, command="git log")
        self.assertGreater(item.visible_chars, 3500)
        self.assertLess(item.visible_chars, len(log))
        for i in range(150):
            self.assertIn(f"subject number {i}\n", item.text.replace(" [+1 body lines]", ""))
        matches = "".join(f"deep/path/to/module_{i % 9}.py:{i}:value {i}\n" for i in range(600))
        item = compress(matches, command="rg value")
        for i in range(600):
            self.assertIn(f"{i}:value {i}\n", item.text)

    def test_explicit_size_is_honored_by_a_view_that_can_shrink(self):
        matches = "".join(f"deep/path/to/module_{i % 9}.py:{i}:value {i}\n" for i in range(600))
        item = compress(matches, command="rg value", max_len=800)
        self.assertLess(item.visible_chars, 900)
        self.assertIn("deep/path/to/module_8.py", item.text)

    def test_hook_policy_has_no_lossy_preview_without_explicit_opt_in(self):
        text = "\n".join(f"row {i} of a listing" for i in range(600))
        self.assertEqual(compress(text, command="git log --oneline", generic=False, cleanup=True).text, text)
        self.assertLess(compress(text, generic=True).visible_chars, len(text))

    def test_cleanup_folds_repeats_only_when_asked(self):
        text = "same line again\n" * 400
        self.assertEqual(compress(text, generic=False).text, text)
        folded = compress(text, generic=False, cleanup=True)
        self.assertIn("repeated 399 more times", folded.text)
        self.assertEqual(get_receipt(folded.receipt), text)

    def test_small_output_is_untouched(self):
        text = "a.py:1:x\na.py:2:y\n"
        self.assertEqual(compress(text, command="rg x").text, text)

    def test_timestamps_do_not_hide_a_repeated_log_line(self):
        text = "\n".join(f"2026-10-26 12:00:{i % 60:02d},123 INFO heartbeat ok" for i in range(500))
        item = compress(text, mode="logs", max_len=600)
        self.assertLess(item.visible_chars, 600)
        self.assertIn("repeated 499 additional times", item.text)
        self.assertEqual(get_receipt(item.receipt), text)

    def test_error_in_the_middle_of_a_large_output_survives(self):
        text = "\n".join(["2026-10-26 INFO step"] * 3000 + ["2026-10-26 ERROR E_MIDDLE_42 at /srv/app/x.py:9"]
                         + ["2026-10-26 INFO step"] * 3000)
        item = compress(text, mode="auto")
        self.assertIn("E_MIDDLE_42 at /srv/app/x.py:9", item.text)
        self.assertEqual(get_receipt(item.receipt), text)


class StoreLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def _noise(self, seed, size=700_000):
        import random
        rng = random.Random(seed)
        return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(size))

    def test_no_quota_means_nothing_is_evicted(self):
        ids = [save_receipt(self._noise(i), source="t", mode="plain", visible_chars=1) for i in range(3)]
        self.assertTrue(all(get_receipt(i) for i in ids))

    def test_quota_evicts_oldest_receipts_only_when_opted_in(self):
        with patch.dict(os.environ, {"SBS_RECEIPT_MAX_MB": "1"}):
            ids = [save_receipt(self._noise(i), source="t", mode="plain", visible_chars=1) for i in range(4)]
            self.assertIsNone(get_receipt(ids[0]))
            self.assertIsNotNone(get_receipt(ids[-1]))
            self.assertLessEqual(report()["stored_bytes"], 1024 * 1024)

    def test_output_above_quota_stays_whole_instead_of_losing_its_original(self):
        text = "\n".join(self._noise(i, 60) for i in range(30000))
        with patch.dict(os.environ, {"SBS_RECEIPT_MAX_MB": "1"}):
            with self.assertRaises(ValueError):
                compress(text, mode="plain")

    def test_invalid_quota_is_rejected(self):
        with patch.dict(os.environ, {"SBS_RECEIPT_MAX_MB": "lots"}):
            with self.assertRaises(ValueError):
                save_receipt("x", source="t", mode="plain", visible_chars=1)

    def test_purge_returns_space_to_the_disk(self):
        for i in range(3):
            save_receipt(self._noise(i), source="t", mode="plain", visible_chars=1)
        before = (Path(self.temp.name) / "store.sqlite3").stat().st_size
        import sqlite3
        conn = sqlite3.connect(Path(self.temp.name) / "store.sqlite3")
        conn.execute("UPDATE receipts SET timestamp = 1")
        conn.commit()
        conn.close()
        self.assertEqual(purge(days=1), 3)
        self.assertLess((Path(self.temp.name) / "store.sqlite3").stat().st_size, before / 2)


if __name__ == "__main__":
    unittest.main()
