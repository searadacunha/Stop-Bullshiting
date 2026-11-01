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


def _chunks(limit: int, max_bytes: int):
    used = 0
    for identifier, stamp, source, text in iter_receipts(limit):
        used += len(text)
        if used > max_bytes:
            return
        lines = text.split("\n")
        for start in range(0, len(lines), WINDOW):
            yield identifier, stamp, source, start, "\n".join(lines[start:start + WINDOW])


def _fts_rank(chunks: list[tuple], words: list[str], limit: int) -> list[tuple[int, float]] | None:
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
            rows = conn.execute("SELECT rowid, bm25(chunks) FROM chunks WHERE chunks MATCH ? "
                                "ORDER BY bm25(chunks) LIMIT ?",
                                (joiner.join(f'"{word}"' for word in words), limit)).fetchall()
            if rows:
                return [(row[0], -row[1]) for row in rows]
        return []
    except sqlite3.OperationalError:
        return None  # this SQLite build has no FTS5
    finally:
        conn.close()


def _plain_rank(chunks: list[tuple], words: list[str], limit: int) -> list[tuple[int, float]]:
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

    return scored(True) or scored(False)


def find(query: str, *, limit: int = 5, receipts: int = 200, max_bytes: int = 64 * 1024 * 1024,
         max_chars: int = 6000, use_fts: bool = True) -> dict:
    """Return the best-matching exact passages across recent receipts, within a size limit."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    words = WORD.findall(query)
    if not words:
        raise ValueError("query needs at least one word")
    chunks = list(_chunks(receipts, max_bytes))
    ranked = _fts_rank(chunks, words, limit) if use_fts else None
    engine = "fts5"
    if ranked is None:
        ranked = _plain_rank(chunks, words, limit)
        engine = "python"
    hits, used = [], 0
    for index, score in ranked:
        identifier, stamp, source, start, body = chunks[index]
        numbered = "\n".join(f"{start + offset + 1}: {line}" for offset, line in enumerate(body.split("\n")))
        if hits and used + len(numbered) > max_chars:
            break
        hits.append({"receipt": identifier, "timestamp": stamp, "source": source, "start": start + 1,
                     "end": start + body.count("\n") + 1, "score": round(score, 3), "text": numbered[:max_chars]})
        used += len(numbered)
    count_event("find", "find")
    return {"query": query, "engine": engine, "searched_chunks": len(chunks), "hits": hits}


def render(result: dict) -> str:
    if not result["hits"]:
        return f"[SBS find: no match in {result['searched_chunks']} passages]"
    sections = [f"{hit['receipt']} {hit['source']} lines {hit['start']}-{hit['end']}\n{hit['text']}"
                for hit in result["hits"]]
    return "\n\n".join(sections)
