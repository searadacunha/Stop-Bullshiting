#!/usr/bin/env python3
"""Replay successful tool results from local Claude Code logs through SBS policies.

Prints aggregate counts only: no command text and no output content leaves this
process or reaches a file. Nothing is stored. It measures what each policy would
have changed, not what a model would then have done.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("SBS_MIN_GAIN", "0.12")

from stop_bullshiting.compress import IMPORTANT, SENSITIVE, compress  # noqa: E402
from stop_bullshiting.filters import GREP_LINE, classify, clean  # noqa: E402
from stop_bullshiting.hooks import safe_read_command  # noqa: E402

BUDGET = 3500
MINIMUM = 1400


def tool_results(path: Path):
    calls: dict = {}
    done: set = set()  # resumed sessions log the same result again: count each call once
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    calls[part.get("id")] = (part.get("name"), part.get("input") or {})
                elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
                    if part["tool_use_id"] in done:
                        continue
                    done.add(part["tool_use_id"])
                    body = part.get("content")
                    if isinstance(body, list):
                        body = "\n".join(p.get("text", "") for p in body if isinstance(p, dict))
                    if isinstance(body, str):
                        yield (*calls[part["tool_use_id"]], body, bool(part.get("is_error")))


def diagnostics_lost(original: str, view: str) -> int:
    """Diagnostic lines absent from a view; a search view prints the path once, so compare without it."""
    lost = 0
    for line in original.split("\n"):
        if IMPORTANT.search(line) and clean(line).strip() not in view:  # colors are not content
            match = GREP_LINE.match(line)
            if not (match and f"{match.group('line')}:{match.group('text')}" in view):
                lost += 1
    return lost


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument("--sessions", type=int, default=260, help="Most recent session logs to replay")
    args = parser.parse_args()
    files = sorted(args.root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)[-args.sessions:]
    seen, visible = Counter(), Counter()
    calls, lost = Counter(), Counter()
    by_kind, by_kind_after = Counter(), Counter()
    elapsed = 0.0
    for file in files:
        for tool, tool_input, text, failed in tool_results(file):
            if tool not in ("Bash", "Read", "Grep"):  # Read is counted, not changed by SBS
                continue
            seen[tool] += len(text)
            calls[tool] += 1
            if failed or SENSITIVE.search(text):
                continue
            started = time.perf_counter()
            if tool == "Bash":
                command = tool_input.get("command", "")
                # The previous hook previewed allowlisted commands only.
                current = compress(text, max_len=BUDGET, minimum=MINIMUM, store=False) if safe_read_command(command) else None
                if current and current.text != text:
                    visible["allowlist_only_saved"] += len(text) - len(current.text)
                    calls["allowlist_only_reduced"] += 1
                # Exactly what the hook does with this result.
                new = compress(text, store=False, command=command, generic=False,
                               cleanup=safe_read_command(command))
                if new.text != text:
                    kind = classify(command) or new.mode
                    calls["command_view_reduced"] += 1
                    visible["command_view_saved"] += len(text) - len(new.text)
                    by_kind[kind] += len(text)
                    by_kind_after[kind] += len(new.text)
                    lost["command_view"] += diagnostics_lost(text, new.text)
                if len(text) > MINIMUM:
                    cleaned = clean(text)
                    if len(cleaned) <= len(text) * 0.88:
                        calls["cleanup_reduced"] += 1
                        visible["cleanup_saved"] += len(text) - len(cleaned)
            elapsed += time.perf_counter() - started
    total = sum(seen.values())
    bash = seen["Bash"]
    report = {
        "sessions": len(files), "calls": {k: calls[k] for k in ("Bash", "Read", "Grep")},
        "chars_seen": dict(seen),
        "bash_saved_chars": {"allowlist_only": visible["allowlist_only_saved"],
                             "command_views": visible["command_view_saved"],
                             "cleanup_for_every_command": visible["cleanup_saved"]},
        "bash_saved_pct_of_bash": {
            "allowlist_only": round(100 * visible["allowlist_only_saved"] / max(1, bash), 2),
            "command_views": round(100 * visible["command_view_saved"] / max(1, bash), 2),
            "cleanup_for_every_command": round(100 * visible["cleanup_saved"] / max(1, bash), 2)},
        "reduced_calls": {"allowlist_only": calls["allowlist_only_reduced"],
                          "command_views": calls["command_view_reduced"],
                          "cleanup_for_every_command": calls["cleanup_reduced"]},
        "command_view_by_format": {k: {"before": by_kind[k], "after": by_kind_after[k]} for k in by_kind},
        "diagnostic_lines_absent_from_views": lost["command_view"],
        "saved_pct_of_all_replayed_chars": round(100 * visible["command_view_saved"] / max(1, total), 2),
        "processing_seconds": round(elapsed, 2),
        "scope": "characters in tool results; not provider tokens, cost or task outcomes",
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
