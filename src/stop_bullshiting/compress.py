"""Original compression heuristics for large tool outputs.

These are lossy *views* of tool results. The full original is saved under a
local receipt unless the caller explicitly disables storage. No model calls.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .config import max_chars as default_max_chars, min_chars as default_min_chars
from .config import min_gain
from .store import save_receipt

IMPORTANT = re.compile(
    r"(?i)(\b(error|fatal|failed|failure|exception|traceback|panic|warning|warn|"
    r"assertion|passed|summary|total|collected|permission denied|not found|"
    r"no such file|exit code)\b|\bE\s+\w+Error\b|^\s*\[fail\])"
)
# Avoid creating another local copy of recognizable credentials. This is not a
# general secret scanner and does NOT redact the command output sent to the agent.
# An uncertain match is deliberately left byte-for-byte unchanged.
SENSITIVE = re.compile(
    r"(?im)(?:-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
    r"|\\bauthorization\\s*:\\s*(?:bearer|basic)\\s+\\S{12,}"
    r"|[\\\"']?(?:x-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password)[\\\"']?\\s*[:=]\\s*[\\\"']?[A-Za-z0-9._~/+=-]{16,})"
)

JSON_KEEP = {"error", "errors", "warning", "warnings", "failed", "failures",
             "status", "summary", "message", "traceback", "exception", "tests",
             "count", "total", "path", "exit_code", "success", "name", "id"}


@dataclass(frozen=True)
class Compression:
    text: str
    mode: str
    original_chars: int
    visible_chars: int
    receipt: str | None = None

    @property
    def saved_percent(self) -> float:
        if not self.original_chars:
            return 0.0
        return max(0., 100 * (1 - self.visible_chars / self.original_chars))


def detect(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith(('{', '[')):
        try:
            json.loads(stripped)
            return "json"
        except (ValueError, RecursionError):
            pass
    if re.search(r"(?m)^diff --git |^@@ [-+]\d", text):
        return "diff"
    if re.search(r"(?mi)^(ERROR|WARN|INFO|DEBUG|FAILED|Traceback|\d{4}-\d\d-\d\d)", text):
        return "logs"
    if re.search(r"(?m)^[ \t]*(?:class |def |async def |from [\w.]+ import |import \w+)", text):
        return "code"
    # Common JavaScript/TypeScript declarations, including indented excerpts.
    # Require declaration syntax so prose such as "let us compare" stays plain.
    if re.search(
        r"(?m)^[ \t]*(?:export[ \t]+(?:default[ \t]+)?)?"
        r"(?:(?:async[ \t]+)?function(?![\w$])[ \t]*(?:\*[ \t]*)?"
        r"(?:[A-Za-z_$][\w$]*[ \t]*)?\("
        r"|(?:const|let|var)[ \t]+[A-Za-z_$][\w$]*(?:[ \t]*:[^=\n]+)?[ \t]*="
        r"|(?:abstract[ \t]+)?(?:class|interface)[ \t]+[A-Za-z_$][\w$]*[^\n;]*\{)",
        text,
    ):
        return "code"
    if "\n" in text and "," in text.splitlines()[0] and len(text.splitlines()) > 6:
        return "csv"
    return "plain"


def _short(line: str, cap: int = 550) -> str:
    if len(line) <= cap:
        return line
    return line[:cap - 65] + f" … [line truncated; original {len(line)} chars]"


def _selection(lines: list[str], *, budget: int, mode: str) -> str | None:
    if not lines:
        return ""
    top, bottom = (10, 12) if mode not in ("diff", "code") else (8, 9)
    scores: dict[int, int] = {}
    required: set[int] = set()
    for i, line in enumerate(lines):
        if i < top or i >= len(lines) - bottom:
            scores[i] = 2
        if IMPORTANT.search(line):
            scores[i] = 7
            if mode in ("logs", "plain"):
                required.add(i)
            if i and mode in ("logs", "plain"):
                scores[i-1] = max(scores.get(i-1, 0), 1)
            if i + 1 < len(lines) and mode in ("logs", "plain"):
                scores[i+1] = max(scores.get(i+1, 0), 1)
        if mode == "diff" and (line.startswith(("diff --git ", "@@ ", "--- ", "+++ "))):
            scores[i] = 6
        if mode == "code" and re.match(r"^\s*(class |def |async def |import |from |@)", line):
            scores[i] = 5
    ordered = sorted(scores, key=lambda i: (-scores[i], i))
    chosen: set[int] = set()
    estimated = 0
    for i in ordered:
        cost = len(lines[i] if i in required else _short(lines[i])) + 2
        if estimated + cost > max(120, budget - 90):
            if i in required:
                return None
            continue
        chosen.add(i)
        estimated += cost
    if not chosen:
        return "\n".join(_short(x) for x in lines[-3:])[:budget]
    result: list[str] = []
    cursor = 0
    for i in sorted(chosen):
        if i > cursor:
            result.append(f"... [{i - cursor} lines omitted] ...")
        result.append(lines[i] if i in required else _short(lines[i]))
        cursor = i + 1
    if cursor < len(lines):
        result.append(f"... [{len(lines) - cursor} lines omitted] ...")
    view = "\n".join(result)
    # Omission markers also consume the budget. Bypass rather than letting the
    # final head/tail fallback discard a diagnostic selected above.
    if required and len(view) > budget:
        return None
    return view


def _json_is_anomaly(item) -> bool:
    if not isinstance(item, dict):
        return False
    for key, value in item.items():
        key = key.casefold()
        if key in {"error", "errors", "exception", "traceback", "failed", "failures"} and value:
            return True
        if key == "status" and isinstance(value, str) and value.strip().casefold() in {
            "failed", "failure", "error", "fatal", "errored",
        }:
            return True
        if key in {"success", "ok"} and value is False:
            return True
        if key in {"exit_code", "returncode"} and isinstance(value, int) and value != 0:
            return True
    return False


def _json_anomaly_nodes(item) -> set[int]:
    """Find diagnostics and their container ancestors without modifying values."""
    nodes: set[int] = set()

    def visit(value) -> bool:
        if not isinstance(value, (dict, list)):
            return False
        affected = _json_is_anomaly(value)
        for child in value.values() if isinstance(value, dict) else value:
            if visit(child):
                affected = True
        if affected:
            nodes.add(id(value))
        return affected

    visit(item)
    return nodes


def _json_shrink(item, *, depth: int, width: int, max_depth: int = 6,
                 anomalies: set[int]):
    protected = id(item) in anomalies
    # Diagnostic records are atomic: shortening their values can hide the cause
    # or change an identifier. A view that cannot hold them is rejected below.
    if _json_is_anomaly(item) or (protected and depth >= max_depth):
        return item
    if depth >= max_depth:
        if isinstance(item, dict):
            return {"_sbs_omitted_keys": len(item)}
        if isinstance(item, list):
            return [{"_sbs_omitted_items": len(item)}]
        if isinstance(item, str) and len(item) > width:
            return item[:width] + "…[truncated]"
        return item
    if isinstance(item, dict):
        keys = list(item)
        priority = [k for k in keys if k.casefold() in JSON_KEEP or id(item[k]) in anomalies]
        ordinary = [k for k in keys if k not in priority]
        selected = priority + ordinary[:max(2, width // 95)]
        result = {
            k: item[k] if protected and k.casefold() in JSON_KEEP
            and not isinstance(item[k], (dict, list)) else
            _json_shrink(item[k], depth=depth+1, width=width,
                         max_depth=max_depth, anomalies=anomalies)
            for k in keys if k in selected
        }
        omitted = len(keys) - len(selected)
        if omitted > 0:
            result["_sbs_omitted_keys"] = omitted
        return result
    if isinstance(item, list):
        keep = max(1, min(6, width // 180))
        selected = set(range(min(keep, len(item))))
        selected.update(range(max(0, len(item) - keep), len(item)))
        selected.update(i for i, value in enumerate(item) if id(value) in anomalies)
        result = []
        cursor = 0
        for i in sorted(selected):
            if i > cursor:
                result.append({"_sbs_omitted_items": i - cursor})
            value = item[i]
            result.append(value if id(value) in anomalies else
                          _json_shrink(value, depth=depth+1, width=width,
                                       max_depth=max_depth, anomalies=anomalies))
            cursor = i + 1
        if cursor < len(item):
            result.append({"_sbs_omitted_items": len(item) - cursor})
        return result
    if isinstance(item, str) and len(item) > width:
        return item[:width] + "…[truncated]"
    return item


def _json_compact(text: str, budget: int) -> str:
    faithful_numbers = True

    def parse_float(value: str) -> float:
        nonlocal faithful_numbers
        number = float(value)
        try:
            if Decimal(value) != Decimal(str(number)):
                faithful_numbers = False
        except InvalidOperation:
            faithful_numbers = False
        return number

    obj = json.loads(text, parse_float=parse_float)
    anomalies = _json_anomaly_nodes(obj)
    # The decoder uses floats; do not emit changed numeric values alongside a
    # diagnostic when the original JSON number cannot survive that round trip.
    if anomalies and not faithful_numbers:
        return ""
    for width, depth in ((240, 5), (140, 4), (65, 3), (25, 2)):
        shrunk = _json_shrink(obj, depth=0, width=width, max_depth=depth, anomalies=anomalies)
        result = json.dumps(shrunk, ensure_ascii=False, separators=(",", ":"), indent=2)
        if len(result) <= budget:
            return result
    return ""


def _csv_compact(text: str, budget: int) -> str:
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return ""
    if not rows:
        return ""
    sample = rows[:5] + ([["...", f"{len(rows) - 9} rows omitted"]] if len(rows) > 9 else []) + rows[-4:]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(sample)
    return out.getvalue()[:budget]


def _view(text: str, mode: str, budget: int) -> str | None:
    if mode == "json":
        try:
            # Never send valid JSON through the line/head-tail fallback: it can
            # remove diagnostics that did not fit the structured view.
            return _json_compact(text, budget) or None
        except RecursionError:
            return None
        except ValueError:
            return _selection(text.splitlines(), budget=budget, mode="plain")
    if mode == "csv":
        return _csv_compact(text, budget) or _selection(text.splitlines(), budget=budget, mode="plain")
    # Consecutive repeated log lines can be condensed without affecting key errors.
    lines = text.splitlines()
    if mode == "logs":
        reduced: list[str] = []
        last = None
        count = 0
        for line in lines:
            if line == last and not IMPORTANT.search(line):
                count += 1
                continue
            if count:
                reduced.append(f"... [previous line repeated {count} additional times]")
            reduced.append(line)
            last = line
            count = 0
        if count:
            reduced.append(f"... [previous line repeated {count} additional times]")
        lines = reduced
    return _selection(lines, budget=budget, mode=mode)


def compress(text: str, *, mode: str = "auto", max_len: int | None = None,
             minimum: int | None = None, source: str = "stdin", store: bool = True) -> Compression:
    """Return compacted view; store entire original locally if changed.

    If the text doesn't shrink enough or diagnostics cannot fit faithfully,
    return it verbatim with no receipt, even above the requested view size.
    """
    if mode not in ("auto", "json", "logs", "diff", "code", "csv", "plain"):
        raise ValueError(f"Unsupported mode {mode!r}")
    # Do not persist recognizable secret material in reversible local receipts.
    # Passing through also avoids removing diagnostic context during reduction.
    if SENSITIVE.search(text):
        return Compression(text, "none", len(text), len(text))
    maximum = default_max_chars() if max_len is None else max(300, max_len)
    threshold = default_min_chars() if minimum is None else max(0, minimum)
    if len(text) <= threshold or len(text) <= maximum:
        return Compression(text, "none", len(text), len(text))
    actual_mode = detect(text) if mode == "auto" else mode
    if mode == "auto" and actual_mode in ("code", "diff", "csv"):
        return Compression(text, "none", len(text), len(text))
    if mode in ("auto", "logs", "plain") and "Traceback (most recent call last):" in text:
        return Compression(text, "none", len(text), len(text))
    view = _view(text, actual_mode, maximum - 135)
    if view is None:
        return Compression(text, "none", len(text), len(text))
    view = view.strip("\n")
    if not view or len(view) > maximum - 100:
        # Last-resort transparent truncation, including the tail for diagnostics.
        half = (maximum - 190) // 2
        view = text[:half] + "\n... [middle omitted] ...\n" + text[-half:]
    if len(view) >= len(text) * (1 - min_gain()):
        return Compression(text, "none", len(text), len(text))
    # The receipt holds the *complete* UTF-8 original; no remote service is used.
    preview_marker = (f"\n[SBS {actual_mode}: {len(text)} → {len(view)} chars; "
                      f"full original: sbs show {'0' * 14}]")
    receipt = save_receipt(text, source=source, mode=actual_mode,
                           visible_chars=len(view) + len(preview_marker)) if store else None
    extra = (f"\n[SBS {actual_mode}: {len(text)} → {len(view)} chars; "
             f"full original: sbs show {receipt}]" if receipt else
             f"\n[SBS {actual_mode}: {len(text)} → {len(view)} chars; original not stored]")
    output = (view + extra)[:maximum]
    # Account for actual visible text. The database records the view+approx marker,
    # reporting is explicitly approximate where the exact marker length varies.
    return Compression(output, actual_mode, len(text), len(output), receipt)
