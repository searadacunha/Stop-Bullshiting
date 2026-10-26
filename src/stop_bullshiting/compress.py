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

from .config import max_chars as default_max_chars, min_chars as default_min_chars
from .config import min_gain
from .store import save_receipt

IMPORTANT = re.compile(
    r"(?i)(\b(error|fatal|failed|failure|exception|traceback|panic|warning|warn|"
    r"assertion|passed|summary|total|collected|permission denied|not found|"
    r"no such file|exit code)\b|\bE\s+\w+Error\b|^\s*\[fail\])"
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
    if re.search(r"(?m)^(?:class |def |async def |from \w+ import |import \w+)", text):
        return "code"
    if "\n" in text and "," in text.splitlines()[0] and len(text.splitlines()) > 6:
        return "csv"
    return "plain"


def _short(line: str, cap: int = 550) -> str:
    if len(line) <= cap:
        return line
    return line[:cap - 65] + f" … [line truncated; original {len(line)} chars]"


def _selection(lines: list[str], *, budget: int, mode: str) -> str:
    if not lines:
        return ""
    top, bottom = (10, 12) if mode not in ("diff", "code") else (8, 9)
    scores: dict[int, int] = {}
    for i, line in enumerate(lines):
        if i < top or i >= len(lines) - bottom:
            scores[i] = 2
        if IMPORTANT.search(line):
            scores[i] = 7
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
        cost = len(_short(lines[i])) + 2
        if estimated + cost > max(120, budget - 90):
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
        result.append(_short(lines[i]))
        cursor = i + 1
    if cursor < len(lines):
        result.append(f"... [{len(lines) - cursor} lines omitted] ...")
    return "\n".join(result)


def _json_shrink(item, *, depth: int, width: int, max_depth: int = 6):
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
        priority = [k for k in keys if k.casefold() in JSON_KEEP]
        ordinary = [k for k in keys if k not in priority]
        selected = priority + ordinary[:max(2, width // 95)]
        result = {k: _json_shrink(item[k], depth=depth+1, width=width, max_depth=max_depth)
                  for k in selected}
        omitted = len(keys) - len(selected)
        if omitted > 0:
            result["_sbs_omitted_keys"] = omitted
        return result
    if isinstance(item, list):
        keep = max(1, min(6, width // 180))
        if len(item) <= 2 * keep:
            return [_json_shrink(x, depth=depth+1, width=width, max_depth=max_depth) for x in item]
        left = [_json_shrink(x, depth=depth+1, width=width, max_depth=max_depth) for x in item[:keep]]
        right = [_json_shrink(x, depth=depth+1, width=width, max_depth=max_depth) for x in item[-keep:]]
        return left + [{"_sbs_omitted_items": len(item) - 2 * keep}] + right
    if isinstance(item, str) and len(item) > width:
        return item[:width] + "…[truncated]"
    return item


def _json_compact(text: str, budget: int) -> str:
    obj = json.loads(text)
    for width, depth in ((240, 5), (140, 4), (65, 3), (25, 2)):
        shrunk = _json_shrink(obj, depth=0, width=width, max_depth=depth)
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


def _view(text: str, mode: str, budget: int) -> str:
    if mode == "json":
        try:
            return _json_compact(text, budget) or _selection(text.splitlines(), budget=budget, mode="plain")
        except (ValueError, RecursionError):
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

    If the text doesn't shrink enough, return it verbatim with no receipt.
    """
    if mode not in ("auto", "json", "logs", "diff", "code", "csv", "plain"):
        raise ValueError(f"Unsupported mode {mode!r}")
    maximum = default_max_chars() if max_len is None else max(300, max_len)
    threshold = default_min_chars() if minimum is None else max(0, minimum)
    if len(text) <= threshold or len(text) <= maximum:
        return Compression(text, "none", len(text), len(text))
    actual_mode = detect(text) if mode == "auto" else mode
    view = _view(text, actual_mode, maximum - 135).strip("\n")
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
