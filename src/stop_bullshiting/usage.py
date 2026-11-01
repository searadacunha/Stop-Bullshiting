"""Read actual token counters from available local agent session logs.

No token estimation: parse explicit provider-reported usage fields only.
Formats may change; unsupported records are ignored and files that cannot be
read are reported, never silently dropped from the totals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator


def _records(file: Path, needle: str, skipped: list[str]) -> Iterator[dict]:
    """Stream one log line by line, parsing only lines that can hold a counter."""
    try:
        with file.open(encoding="utf-8", errors="replace") as inp:
            for line in inp:
                if needle not in line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        skipped.append(str(file))


def _counter(obj: dict, name: str) -> int:
    val = obj.get(name, 0)
    return val if isinstance(val, int) and val >= 0 and not isinstance(val, bool) else 0


def _in_window(stamp, since: str | None) -> bool:
    return since is None or (isinstance(stamp, str) and stamp[:10] >= since)


def _claude_roots() -> list[Path]:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        bases = [Path(part).expanduser() for part in configured.split(",") if part.strip()]
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        bases = [Path(xdg).expanduser() / "claude" if xdg else Path.home() / ".config" / "claude",
                 Path.home() / ".claude"]
    return [base / "projects" for base in bases]


def _unique_logs(roots: list[Path]) -> list[Path]:
    seen: dict[Path, None] = {}
    for root in roots:
        if root.exists():
            for file in root.glob("**/*.jsonl"):
                seen.setdefault(file.resolve(), None)
    return list(seen)


def claude_usage(root: Path | None = None, *, since: str | None = None) -> dict:
    files = _unique_logs([root] if root else _claude_roots())
    # Resumed sessions and subagent logs repeat a response, so identity spans all files.
    by_message: dict[tuple, tuple] = {}
    skipped: list[str] = []
    for file in files:
        for record in _records(file, '"usage"', skipped):
            message = record.get("message") or {}
            if record.get("type") != "assistant" or not isinstance(message, dict):
                continue
            usage = message.get("usage") or {}
            identifier = message.get("id")
            if not isinstance(usage, dict) or not identifier or not _in_window(record.get("timestamp"), since):
                continue
            # Streaming writes a response several times; keep its most complete record,
            # and the main thread's copy of equal ones.
            counters = (_counter(usage, "input_tokens"), _counter(usage, "output_tokens"),
                        _counter(usage, "cache_read_input_tokens"), _counter(usage, "cache_creation_input_tokens"))
            key = (str(identifier), str(record.get("requestId") or ""))
            old = by_message.get(key)
            main_thread = not record.get("isSidechain")
            rank = (sum(counters), main_thread)
            if old is None or rank > old[1]:
                by_message[key] = (counters, rank)
    fields = ("input", "output", "cache_read", "cache_write")
    return {"agent": "claude", "logs_scanned": len(files), "messages": len(by_message),
            "totals": {field: sum(entry[0][index] for entry in by_message.values())
                       for index, field in enumerate(fields)},
            "unreadable_logs": len(skipped), "since": since, "counter_type": "recorded_provider_usage"}


def codex_usage(root: Path | None = None, *, since: str | None = None) -> dict:
    if root:
        bases = [root]
    else:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        bases = [home / "sessions", home / "archived_sessions"]
    files = _unique_logs(bases)
    totals = {"input": 0, "output": 0, "cache_read": 0, "reasoning": 0}
    skipped: list[str] = []
    for file in files:
        # Codex emits rolling cumulative totals per session. Never add all events.
        first: dict = {}
        last: dict = {}
        for record in _records(file, "token_count", skipped):
            payload = record.get("payload") or {}
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            current = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(current, dict):
                if since is not None and not _in_window(record.get("timestamp"), since):
                    first = current  # the window starts after this cumulative total
                    continue
                last = current
        pairs = (("input", "input_tokens"), ("output", "output_tokens"),
                 ("cache_read", "cached_input_tokens"), ("reasoning", "reasoning_output_tokens"))
        for field, name in pairs:
            if last:
                totals[field] += max(0, _counter(last, name) - _counter(first, name))
    return {"agent": "codex", "logs_scanned": len(files), "messages": None, "totals": totals,
            "unreadable_logs": len(skipped), "since": since, "counter_type": "recorded_provider_usage",
            # A forked session restarts from its parent's totals, which this cannot separate.
            "note": "input includes cache_read; forked sessions may repeat their parent's totals"}
