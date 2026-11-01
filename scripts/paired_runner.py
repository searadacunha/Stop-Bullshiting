#!/usr/bin/env python3
"""Run the same small coding tasks with and without the SBS hook on real Claude Code.

Each trial gets a fresh synthetic repository, no user settings and no MCP servers.
Tokens and cost are what the client reports, and success comes from a check on the
answer or on the repository, not from the model's opinion of itself. This spends
quota. Summarize the output with scripts/paired_sessions.py.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ["Read", "Edit"] + [f"Bash({name}:*)" for name in
                              ("python3", "git", "rg", "grep", "sbs", "cat", "sed", "ls", "wc", "head", "tail", "cd")]
GIT_ENV = {"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com", "GIT_COMMITTER_NAME": "Dev",
           "GIT_COMMITTER_EMAIL": "dev@example.com", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=path, env={**os.environ, **GIT_ENV},
                          check=True, capture_output=True, text=True).stdout.strip()


def commit_all(path: Path, message: str) -> None:
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)


def _init(path: Path) -> None:
    git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("fixture\n")
    commit_all(path, "Initial commit")


def build_unittest(path: Path) -> None:
    _init(path)
    (path / "tests").mkdir()
    cases = "".join(f"    def test_case_{i}(self):\n        self.assertEqual({i} + 1, {i + 1})\n\n" for i in range(300))
    (path / "tests" / "test_many.py").write_text(
        "import unittest\n\nclass Many(unittest.TestCase):\n" + cases +
        "    @unittest.skip('requires the ledger database')\n    def test_settlement_export(self):\n        pass\n")


def build_git_log(path: Path) -> None:
    _init(path)
    for i in range(120):
        (path / "file.txt").write_text(f"{i}\n")
        subject = "Fix overflow in ledger rounding" if i == 61 else f"Routine change number {i}"
        commit_all(path, f"{subject}\n\nBody line of change {i}.\nSigned-off-by: Dev <dev@example.com>")


def build_git_status(path: Path) -> None:
    _init(path)
    (path / "src").mkdir()
    (path / "src" / "settlement_core.py").write_text("rate = 1\n")
    commit_all(path, "Add core")
    (path / "src" / "settlement_core.py").write_text("rate = 2\n")
    (path / "data" / "raw").mkdir(parents=True)
    for i in range(240):
        (path / "data" / "raw" / f"export_{i}.csv").write_text("x\n")


def build_search(path: Path) -> None:
    _init(path)
    folder = path / "src" / "services" / "payments" / "internal"
    folder.mkdir(parents=True)
    for number in range(14):
        count = 57 if number == 9 else 20 + number
        (folder / f"module_{number}.py").write_text("".join(f"value_{i} = lookup_rate({i})\n" for i in range(count)))


def build_diff(path: Path) -> None:
    _init(path)
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("SETTLEMENT_RATE = 17\n")
    (path / "package-lock.json").write_text("".join(f'"pkg-{i}": "1.0.{i}",\n' for i in range(900)))
    commit_all(path, "Add app")
    (path / "src" / "app.py").write_text("SETTLEMENT_RATE = 23\n")
    (path / "package-lock.json").write_text("".join(f'"pkg-{i}": "1.0.{i + 1}",\n' for i in range(900)))


def build_fix(path: Path) -> None:
    _init(path)
    (path / "tests").mkdir()
    (path / "ledger.py").write_text("def rounded_total(values):\n    return round(sum(values) - 1, 2)\n")
    cases = "".join(f"    def test_ok_{i}(self):\n        self.assertEqual(rounded_total([{i}, 1]), {i})\n\n" for i in range(0))
    filler = "".join(f"    def test_filler_{i}(self):\n        self.assertTrue(True)\n\n" for i in range(250))
    (path / "tests" / "test_ledger.py").write_text(
        "import unittest\nfrom ledger import rounded_total\n\nclass Ledger(unittest.TestCase):\n" + cases + filler +
        "    def test_total(self):\n        self.assertEqual(rounded_total([1.5, 2.5]), 4.0)\n")
    commit_all(path, "Add ledger")


def contains(*needles: str):
    return lambda answer, _: all(needle.lower() in answer.lower() for needle in needles)


def log_hash_check(answer: str, path: Path) -> bool:
    return git(path, "log", "--format=%H", "--grep=Fix overflow in ledger rounding") in answer


def tests_pass(_: str, path: Path) -> bool:
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=path,
                          capture_output=True).returncode == 0


TASKS = {
    "unittest-skip": (build_unittest, "Run `python3 -m unittest discover -s tests -v`. Report how many tests ran, "
                      "the name of the skipped test, and the reason it was skipped.",
                      contains("301", "test_settlement_export", "ledger database")),
    "git-log-hash": (build_git_log, "Run `git log` without any options. Report the full 40-character hash of the commit "
                     "whose subject is about the ledger overflow fix.", log_hash_check),
    "git-status": (build_git_status, "Run `git status -uall`. Report which tracked file is modified, and how many untracked "
                   "files are in data/raw.", contains("settlement_core.py", "240")),
    "search-count": (build_search, "Run `rg -n lookup_rate src`. Report which file has the most matches and how many it has.",
                     lambda answer, _: "module_9" in answer and "57" in answer),
    "diff-rate": (build_diff, "Run `git diff`. Report the old and new value of SETTLEMENT_RATE, and which other file changed.",
                  contains("17", "23", "package-lock.json")),
    "fix-bug": (build_fix, "Run `python3 -m unittest discover -s tests -v`, then fix the bug in ledger.py so every test "
                "passes. Do not change the tests. Run the tests again to confirm.", tests_pass),
}


def run_trial(task: str, variant: str, repeat: int, model: str, budget: float) -> dict:
    build, prompt, check = TASKS[task]
    with tempfile.TemporaryDirectory(prefix="sbs-paired-") as raw:
        base = Path(raw).resolve()
        project, state, tools = base / "project", base / "state", base / "bin"
        project.mkdir()
        tools.mkdir()
        build(project)
        sha = git(project, "rev-parse", "HEAD")
        shim = tools / "sbs"
        shim.write_text(f'#!/bin/sh\nPYTHONPATH={ROOT / "src"} SBS_HOME={state} exec {sys.executable} -m stop_bullshiting "$@"\n')
        shim.chmod(0o755)
        hooks = {}
        if variant == "sbs":
            command = f'PYTHONPATH={ROOT / "src"} SBS_HOME={state} {sys.executable} -m stop_bullshiting hook claude'
            hooks = {"PostToolUse": [{"matcher": "Bash|Grep", "hooks": [{"type": "command", "command": command, "timeout": 10}]}]}
        settings = base / "settings.json"
        settings.write_text(json.dumps({"hooks": hooks}))
        argv = [shutil.which("claude"), "-p", "--model", model, "--no-session-persistence", "--setting-sources", "project",
                "--settings", str(settings), "--strict-mcp-config", "--disable-slash-commands", "--tools", "Bash,Read,Edit",
                "--allowedTools", *ALLOWED, "--max-budget-usd", str(budget), "--output-format", "stream-json", "--verbose", prompt]
        env = {**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"}
        started = time.time()
        errors = 0
        try:
            proc = subprocess.run(argv, cwd=project, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=240)
            lines = proc.stdout.splitlines()
        except subprocess.TimeoutExpired as exc:
            lines = (exc.stdout or b"").decode("utf-8", "replace").splitlines() if isinstance(exc.stdout, bytes) else []
            errors += 1
        final, tool_calls, restores = {}, 0, 0
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                final = event
            elif event.get("type") == "assistant":
                for part in event.get("message", {}).get("content", []):
                    if part.get("type") == "tool_use":
                        tool_calls += 1
                        if re.search(r"\bsbs\s+(show|find)\b", str(part.get("input", {}).get("command", ""))):
                            restores += 1
        usage = final.get("usage") or {}
        answer = final.get("result") or ""
        try:
            success = bool(final) and not final.get("is_error") and bool(check(answer, project))
        except Exception:
            success = False
        reduced = 0
        if variant == "sbs" and (state / "store.sqlite3").exists():
            sys.path.insert(0, str(ROOT / "src"))
            import sqlite3
            conn = sqlite3.connect(state / "store.sqlite3")
            reduced = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            conn.close()
    total = sum(usage.get(key, 0) for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                              "cache_creation_input_tokens")) if usage else None
    return {"task_id": task, "repeat": repeat, "variant": variant, "success": success, "model": model,
            "task_revision": "v1", "repository_sha": sha, "tokens_total": total,
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"), "cache_write_tokens": usage.get("cache_creation_input_tokens"),
            "cost_usd": final.get("total_cost_usd"), "tool_calls": tool_calls, "restore_calls": restores,
            "elapsed_seconds": round(time.time() - started, 1), "errors": errors + (1 if final.get("is_error") else 0),
            "receipts_created": reduced, "answer": answer}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSONL file to append trials to")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--budget", type=float, default=0.30, help="Spending limit per trial, in USD as reported by the client")
    parser.add_argument("--tasks", nargs="*", default=sorted(TASKS), choices=sorted(TASKS))
    parser.add_argument("--seed", type=int, default=20261101)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    plan = [(task, repeat, variant) for task in args.tasks for repeat in range(args.repeats)
            for variant in rng.sample(["control", "sbs"], 2)]
    # Pairs stay adjacent so both arms meet the same prompt-cache conditions.
    pairs = [plan[i:i + 2] for i in range(0, len(plan), 2)]
    rng.shuffle(pairs)
    with args.output.open("a", encoding="utf-8") as stream:
        for pair in pairs:
            for task, repeat, variant in pair:
                row = run_trial(task, variant, repeat, args.model, args.budget)
                stream.write(json.dumps({key: value for key, value in row.items() if key != "answer"}) + "\n")
                stream.flush()
                print(task, repeat, variant, "ok" if row["success"] else "FAILED", row["tokens_total"], row["cost_usd"], flush=True)
                if not row["success"]:
                    print("  answer:", row.pop("answer")[:300].replace("\n", " "), flush=True)
                row.pop("answer", None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
