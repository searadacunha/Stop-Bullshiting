"""Read actual token counters from available local agent session logs.

No token estimation: parse explicit provider-reported usage fields only.
Formats may change; unsupported records are ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

MAX_LOG_SIZE = 40 * 1024 * 1024
MAX_LOG_FILES = 1500


def _records(file: Path) -> Iterator[dict]:
    try:
        if file.stat().st_size > MAX_LOG_SIZE:
            return
        with file.open(encoding="utf-8", errors="replace") as inp:
            for line in inp:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        yield record
                except ValueError:
                    continue
    except OSError:
        return


def _counter(obj: dict, name: str) -> int:
    val = obj.get(name, 0)
    return val if isinstance(val, int) and val >= 0 and not isinstance(val, bool) else 0


def claude_usage(root: Path | None = None) -> dict:
    base = root or Path.home() / ".claude" / "projects"
    by_message: dict[str, dict] = {}
    sessions = 0
    for file in list(base.glob("**/*.jsonl"))[:MAX_LOG_FILES] if base.exists() else []:
        sessions += 1
        for record in _records(file):
            message = record.get("message") or {}
            if record.get("type") != "assistant" or not isinstance(message, dict):
                continue
            usage = message.get("usage") or {}
            identifier = message.get("id")
            if not isinstance(usage, dict) or not identifier:
                continue
            # Message IDs can occur more than once due to streaming / log updates.
            key = str(file) + ":" + str(identifier)
            counters = {"input": _counter(usage, "input_tokens"),
                        "output": _counter(usage, "output_tokens"),
                        "cache_read": _counter(usage, "cache_read_input_tokens"),
                        "cache_write": _counter(usage, "cache_creation_input_tokens")}
            old = by_message.get(key, {})
            by_message[key] = {field: max(val, old.get(field, 0)) for field, val in counters.items()}
    return {"agent": "claude", "logs_scanned": sessions, "messages": len(by_message),
            "totals": {field: sum(x[field] for x in by_message.values())
                       for field in ("input", "output", "cache_read", "cache_write")},
            "counter_type": "recorded_provider_usage"}


def codex_usage(root: Path | None = None) -> dict:
    base = root or Path.home() / ".codex" / "sessions"
    sessions = 0
    totals = {"input": 0, "output": 0, "cache_read": 0, "reasoning": 0}
    for file in list(base.glob("**/*.jsonl"))[:MAX_LOG_FILES] if base.exists() else []:
        sessions += 1
        # Codex emits rolling cumulative totals per session. Never add all events.
        last: dict = {}
        for record in _records(file):
            payload = record.get("payload") or {}
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            current = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(current, dict):
                last = current
        totals["input"] += _counter(last, "input_tokens")
        totals["output"] += _counter(last, "output_tokens")
        totals["cache_read"] += _counter(last, "cached_input_tokens")
        totals["reasoning"] += _counter(last, "reasoning_output_tokens")
    return {"agent": "codex", "logs_scanned": sessions, "messages": None,
            "totals": totals, "counter_type": "recorded_provider_usage"}
