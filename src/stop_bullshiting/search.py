"""Ranked search across stored originals, without keeping a searchable copy.

Receipts are decompressed for one query and indexed in memory, and the index
disappears with the connection. A persistent full-text index would keep readable
copies of tool output on disk, so there is none.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter

from .store import count_event, iter_receipts

WINDOW = 12
WORD = re.compile(r"\w+", re.UNICODE)


def _chunks(limit: int, max_bytes: int, coverage: dict):
    for identifier, stamp, source, text in iter_receipts(limit, max_bytes=max_bytes):
        coverage["considered_receipts"] += 1
        if text is None:
            coverage["skipped_receipts"] += 1
            continue
        coverage["searched_receipts"] += 1
        coverage["searched_bytes"] += len(text.encode("utf-8", "surrogatepass"))
        lines = text.split("\n")
        for start in range(0, len(lines), WINDOW):
            yield identifier, stamp, source, start, "\n".join(lines[start:start + WINDOW])


def _fts_lines(conn: sqlite3.Connection, index: int, body: str, query: str) -> list[int]:
    """Ask FTS which source lines match, including its Unicode normalization.

    Markers exist only in the ephemeral query result and are never returned to
    the caller. Make them absent from the original so source text cannot be
    mistaken for highlighting.
    """
    opening, closing = "\ufdd0", "\ufdd1"
    while opening in body:
        opening += opening
    while closing in body:
        closing += closing
    marked = conn.execute("SELECT highlight(chunks, 0, ?, ?) FROM chunks "
                          "WHERE rowid = ? AND chunks MATCH ?",
                          (opening, closing, index, query)).fetchone()[0]
    matched, cursor, line = set(), 0, 0
    while (left := marked.find(opening, cursor)) >= 0:
        line += marked.count("\n", cursor, left)
        right = marked.find(closing, left + len(opening))
        last = line + marked.count("\n", left + len(opening), right)
        matched.update(range(line, last + 1))
        cursor, line = right + len(closing), last
    return sorted(matched)


def _fts_rank(chunks: list[tuple], words: list[str], limit: int) -> list[tuple[int, float, list[int]]] | None:
    try:
        conn = sqlite3.connect(":memory:")
    except sqlite3.Error:
        return None
    try:
        conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(body, tokenize='unicode61')")
        conn.executemany("INSERT INTO chunks(rowid, body) VALUES (?, ?)",
                         ((index, chunk[4]) for index, chunk in enumerate(chunks)))
        # Every word when possible; any word when that finds nothing.
        for joiner in (" AND ", " OR "):
            query = joiner.join(f'"{word}"' for word in words)
            rows = conn.execute("SELECT rowid, bm25(chunks) FROM chunks WHERE chunks MATCH ? "
                                "ORDER BY bm25(chunks), rowid LIMIT ?", (query, limit)).fetchall()
            if rows:
                return [(row[0], -row[1], _fts_lines(conn, row[0], chunks[row[0]][4], query)) for row in rows]
        return []
    except (sqlite3.OperationalError, UnicodeError):
        return None  # no FTS5, or text SQLite cannot encode (for example lone surrogates)
    finally:
        conn.close()


def _plain_rank(chunks: list[tuple], words: list[str], limit: int) -> list[tuple[int, float, list[int]]]:
    """BM25 in plain Python for SQLite builds without FTS5."""
    wanted = [word.casefold() for word in words]
    documents = [Counter(token.casefold() for token in WORD.findall(chunk[4])) for chunk in chunks]
    if not documents:
        return []
    average = sum(sum(doc.values()) for doc in documents) / len(documents) or 1.0
    frequency = {word: sum(1 for doc in documents if word in doc) for word in wanted}

    def scored(required: bool) -> list[tuple[int, float]]:
        found = []
        for index, doc in enumerate(documents):
            if not (all if required else any)(word in doc for word in wanted):
                continue
            length = sum(doc.values())
            score = 0.0
            for word in wanted:
                count = doc.get(word, 0)
                if count:
                    idf = math.log(1 + (len(documents) - frequency[word] + 0.5) / (frequency[word] + 0.5))
                    score += idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average))
            found.append((index, score))
        return sorted(found, key=lambda item: (-item[1], item[0]))[:limit]

    ranked = scored(True) or scored(False)
    return [(index, score, [line for line, text in enumerate(chunks[index][4].split("\n"))
                            if any(token.casefold() in wanted for token in WORD.findall(text))])
            for index, score in ranked]


def _passage(body: str, start: int, matched: list[int], budget: int) -> dict | None:
    """Choose complete contiguous lines, prioritizing matching lines over context."""
    lines = body.split("\n")
    sizes = [len(str(start + offset + 1)) + 2 + len(line) for offset, line in enumerate(lines)]
    anchors = set(matched)
    best = None
    # At most WINDOW lines: examine the small set of exact ranges instead of
    # slicing characters off the front and potentially removing the only match.
    for left in range(len(lines)):
        size = 0
        for right in range(left, len(lines)):
            size += sizes[right] + (right > left)
            if size > budget:
                break
            visible = [line for line in anchors if left <= line <= right]
            if not visible:
                continue
            balance = min(min(visible) - left, right - max(visible))
            rank = (len(visible), right - left + 1, balance, -left)
            if best is None or rank > best[0]:
                best = rank, left, right
    if best is None:
        return None
    _, left, right = best
    return {"start": start + left + 1, "end": start + right + 1,
            "text": "\n".join(f"{start + index + 1}: {lines[index]}" for index in range(left, right + 1))}


def find(query: str, *, limit: int = 5, receipts: int = 200, max_bytes: int = 64 * 1024 * 1024,
         max_chars: int = 6000, use_fts: bool = True) -> dict:
    """Return ranked exact passages; ``max_chars`` bounds the entire text render.

    Numbering, headers, and coverage/omission notices count toward that budget.
    The JSON result carries additional metadata and is not a size-bounded wire
    format. ``max_bytes`` limits accepted decompressed UTF-8 bytes, not characters;
    oversized receipts are counted and skipped while older receipts remain eligible.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    for name, value, minimum in (("limit", limit, 1), ("receipts", receipts, 1),
                                 ("max_bytes", max_bytes, 1), ("max_chars", max_chars, 256)):
        if type(value) is not int or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    words = WORD.findall(query)
    if not words:
        raise ValueError("query needs at least one word")
    coverage = {"considered_receipts": 0, "searched_receipts": 0, "searched_bytes": 0,
                "skipped_receipts": 0, "receipt_limit": receipts, "byte_limit": max_bytes}
    chunks = list(_chunks(receipts, max_bytes, coverage))
    ranked = _fts_rank(chunks, words, limit) if use_fts else None
    engine = "fts5"
    if ranked is None:
        ranked = _plain_rank(chunks, words, limit)
        engine = "python"
    omitted = []
    for index, _, matched in ranked:
        identifier, _, _, start, body = chunks[index]
        omitted.append({"receipt": identifier,
                        "start": start + min(matched, default=0) + 1,
                        "end": start + max(matched, default=body.count("\n")) + 1})
    result = {"query": query, "engine": engine, "searched_chunks": len(chunks), "hits": [],
              "coverage": coverage, "omitted_passages": omitted, "max_chars": max_chars}
    # Reserve the notice for all still-hidden matches. A later omission must not
    # force us to truncate or discard an exact passage already selected.
    for position, (index, score, matched) in enumerate(ranked):
        identifier, stamp, source, start, body = chunks[index]
        last = start + body.count("\n") + 1
        candidate = {"receipt": identifier, "timestamp": stamp, "source": source, "start": last,
                     "end": last, "score": round(score, 3), "text": ""}
        remaining_omissions = [item for item in result["omitted_passages"] if item is not omitted[position]]
        preview = {**result, "hits": [*result["hits"], candidate], "omitted_passages": remaining_omissions}
        available = max_chars - len(render(preview))
        passage = _passage(body, start, matched, available)
        if passage is not None:
            candidate.update(passage)
            result["hits"].append(candidate)
            result["omitted_passages"] = remaining_omissions
    count_event("find", "find")
    return result


def render(result: dict) -> str:
    sections = [f"{hit['receipt']} {hit['source']} lines {hit['start']}-{hit['end']}\n{hit['text']}"
                for hit in result["hits"]]
    omitted = result.get("omitted_passages", [])
    if omitted:
        sections.append(f"[SBS find: {len(omitted)} matching passages omitted by max_chars={result['max_chars']}; "
                        f"recover first: sbs show {omitted[0]['receipt']}]")
    elif not sections:
        sections.append(f"[SBS find: no match in {result['searched_chunks']} searched passages]")
    coverage = result.get("coverage", {})
    notices = []
    considered = coverage.get("considered_receipts", 0)
    if considered and considered == coverage.get("receipt_limit"):
        notices.append(f"scope: most recent {considered} receipt{'s' if considered != 1 else ''}")
    if coverage.get("skipped_receipts"):
        notices.append(f"byte limit; {coverage['skipped_receipts']}/{considered} receipts skipped")
    if notices:
        sections.append("[SBS find: " + "; ".join(notices) + ".]")
    return "\n\n".join(sections)
