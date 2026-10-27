"""Exact, bounded views of stored originals without another compression pass."""

from __future__ import annotations


def _integer(name: str, value: int, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def select_receipt(text: str, *, start: int | None = None, end: int | None = None,
                   query: str | None = None, context: int = 2, limit: int = 50,
                   max_chars: int = 20000) -> str:
    """Return numbered, complete source lines and explicit selection metadata.

    Ranges are 1-based and inclusive. A literal, case-sensitive query searches
    within that range; overlapping context windows are merged. ``limit`` bounds
    the number of selected source lines considered, including oversized lines.
    Queries prioritize matching lines, then nearby context, within that limit;
    retained lines are emitted in their original source order.
    ``max_chars`` bounds the entire response, including metadata. A source line
    that cannot fit is identified as omitted rather than silently shortened.

    Callers wanting the unchanged complete original should bypass this function.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if start is not None:
        _integer("start", start, 1)
    if end is not None:
        _integer("end", end, 1)
    if start is not None and end is not None and end < start:
        raise ValueError("end must be greater than or equal to start")
    _integer("context", context, 0)
    _integer("limit", limit, 1)
    _integer("max_chars", max_chars, 256)
    if query is not None:
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a nonempty string")
        if any(separator in query for separator in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
            raise ValueError("query must be a single-line literal")

    # Keep whitespace and original line endings. Only numbering and explanatory
    # metadata are added; content is never stripped or normalized.
    lines = text.splitlines(keepends=True)
    total = len(lines)
    low = (start or 1) - 1
    high = min(end if end is not None else total, total)
    intervals: list[tuple[int, int]] = []
    matching_indices: list[int] = []
    matches = 0
    if query is None:
        if low < high:
            intervals.append((low, high))
    else:
        for index in range(low, high):
            if query not in lines[index]:
                continue
            matches += 1
            matching_indices.append(index)
            left, right = max(low, index - context), min(high, index + context + 1)
            if intervals and left <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(right, intervals[-1][1]))
            else:
                intervals.append((left, right))

    selected = sum(right - left for left, right in intervals)
    if matching_indices:
        # Reserve the line budget for actual matches before their neighbours.
        # Add nearer context first, deduplicate overlapping windows, then put
        # the retained indices back in source order for exact presentation.
        chosen = set(matching_indices[:limit])
        target = min(limit, selected)
        for distance in range(1, min(context, high - low) + 1):
            if len(chosen) >= target:
                break
            for index in matching_indices:
                for neighbor in (index - distance, index + distance):
                    if low <= neighbor < high:
                        chosen.add(neighbor)
                    if len(chosen) >= target:
                        break
                if len(chosen) >= target:
                    break
        intervals = [(index, index + 1) for index in sorted(chosen)]
    shown = 0
    rows: list[str] = []
    used_chars = 0

    def metadata(count: int) -> str:
        match_label = str(matches) if query is not None else "not_searched"
        return (f"[SBS receipt: total_lines={total}; selected_lines={selected}; "
                f"shown_lines={count}; omitted_lines={total - count}; "
                f"match_lines={match_label}]\n")

    def guidance(count: int) -> str:
        if count == selected:
            return ""
        return "[SBS omitted: narrow start/end/query, or use sbs show for the original.]\n"

    considered = 0
    for left, right in intervals:
        for index in range(left, right):
            if considered >= limit:
                break
            considered += 1
            original = lines[index]
            prefix = f"{index + 1}: "
            # Every emitted row ends visibly before the next row/metadata. The
            # source's own ending, including CRLF, is otherwise left untouched.
            ending = "" if original.endswith("\n") else "\n"
            row_chars = len(prefix) + len(original) + len(ending)
            overhead = len(metadata(shown + 1)) + len(guidance(shown + 1))
            if used_chars + row_chars + overhead <= max_chars:
                rows.append(prefix + original + ending)
                used_chars += row_chars
                shown += 1
                continue
            # If the complete line could fit in an otherwise empty response,
            # stop here so the caller can continue from its line number. Truly
            # oversized lines receive a marker, and later short lines may fit.
            standalone_overhead = len(metadata(1)) + len(guidance(1))
            if row_chars + standalone_overhead <= max_chars:
                considered = limit
                break
            marker = (f"[SBS line {index + 1} omitted: {len(original)} chars "
                      f"plus numbering/metadata exceed budget={max_chars}.]\n")
            if used_chars + len(marker) + len(metadata(shown)) + len(guidance(shown)) > max_chars:
                considered = limit
                break
            rows.append(marker)
            used_chars += len(marker)
        if considered >= limit:
            break
    return metadata(shown) + "".join(rows) + guidance(shown)
