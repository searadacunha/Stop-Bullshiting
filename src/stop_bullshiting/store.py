"""User-private SQLite receipts and notes. Originals are compressed, not encrypted."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
import zlib
from contextlib import contextmanager
from typing import Iterator

from .config import ensure_private_dir, home

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    original BLOB NOT NULL,
    original_chars INTEGER NOT NULL,
    visible_chars INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_timestamp_idx ON receipts(timestamp);
CREATE TABLE IF NOT EXISTS notes (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated INTEGER NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS counters (
    day TEXT NOT NULL,
    source TEXT NOT NULL,
    outcome TEXT NOT NULL,
    calls INTEGER NOT NULL,
    original_chars INTEGER NOT NULL,
    visible_chars INTEGER NOT NULL,
    micros INTEGER NOT NULL,
    PRIMARY KEY (day, source, outcome)
);
"""


def _quota_bytes() -> int | None:
    """Opt-in size limit for stored originals; nothing is evicted without it."""
    raw = os.environ.get("SBS_RECEIPT_MAX_MB")
    if raw is None or raw == "":
        return None
    if not raw.isdecimal() or not 1 <= int(raw) <= 1048576:
        raise ValueError("SBS_RECEIPT_MAX_MB must be an integer from 1 to 1048576")
    return int(raw) * 1024 * 1024


def _retention_cutoff() -> int | None:
    """Opt-in receipt TTL; no existing data is deleted without configuration."""
    raw = os.environ.get("SBS_RECEIPT_RETENTION_DAYS")
    if raw is None or raw == "":
        return None
    if not raw.isdecimal() or not 1 <= int(raw) <= 3650:
        raise ValueError("SBS_RECEIPT_RETENTION_DAYS must be an integer from 1 to 3650")
    return int(time.time()) - int(raw) * 86400


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    # Check configuration before creating any file or modifying existing data.
    cutoff = _retention_cutoff()
    directory = ensure_private_dir(home())
    target = directory / "store.sqlite3"
    if target.is_symlink():
        raise ValueError("Refusing a symlinked SQLite store")
    conn = sqlite3.connect(str(target), timeout=10)
    if os.name == "posix":
        os.chmod(target, 0o600)
    conn.executescript(SCHEMA)
    try:
        if cutoff is not None:
            # Receipts only: project notes are deliberately not expired.
            conn.execute("DELETE FROM receipts WHERE timestamp < ?", (cutoff,))
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_receipt(text: str, *, source: str, mode: str, visible_chars: int) -> str:
    """Receipt identifiers are random, not plaintext-derived."""
    identifier = uuid.uuid4().hex[:14]
    # surrogatepass: JSON input can hold lone surrogates, and a receipt must restore them.
    encoded = text.encode("utf-8", errors="surrogatepass")
    blob = zlib.compress(encoded, 6)
    quota = _quota_bytes()
    if quota is not None and len(blob) > quota:
        # Callers keep the unreduced output: no preview without its original.
        raise ValueError("Output exceeds SBS_RECEIPT_MAX_MB")
    with db() as con:
        con.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (identifier, int(time.time()), source[:160], mode, sqlite3.Binary(blob),
             len(text), visible_chars, hashlib.sha256(encoded).hexdigest()),
        )
        if quota is not None:
            stored = con.execute("SELECT COALESCE(SUM(LENGTH(original)),0) FROM receipts").fetchone()[0]
            if stored > quota:
                rows = con.execute("SELECT id, LENGTH(original) FROM receipts WHERE id != ? "
                                   "ORDER BY timestamp, rowid", (identifier,))
                for victim, size in rows.fetchall():
                    if stored <= quota:
                        break
                    con.execute("DELETE FROM receipts WHERE id = ?", (victim,))
                    stored -= size
    return identifier


def get_receipt(identifier: str) -> str | None:
    with db() as con:
        row = con.execute("SELECT original FROM receipts WHERE id = ?", (identifier,)).fetchone()
    if row:
        count_event("restore", "restore")
    return zlib.decompress(row[0]).decode("utf-8", "surrogatepass") if row else None


def _receipt_text(blob: bytes, max_bytes: int | None) -> tuple[str | None, int]:
    """Decode one original, stopping once it cannot fit the optional byte budget."""
    if max_bytes is None:
        encoded = zlib.decompress(blob)
    else:
        decoder = zlib.decompressobj()
        encoded = decoder.decompress(blob, max_bytes + 1)
        if len(encoded) > max_bytes:
            # This original was not searched: do not expand the rest just to
            # validate it. Accepted originals still require a complete stream.
            return None, 0
        if not decoder.eof:
            raise zlib.error("Incomplete or truncated receipt stream")
    return encoded.decode("utf-8", "surrogatepass"), len(encoded)


def iter_receipts(limit: int, *, max_bytes: int | None = None):
    """Yield (id, timestamp, source, text), newest first, one original at a time.

    With ``max_bytes``, accepted text shares a cumulative UTF-8 byte budget
    (using surrogatepass, like storage). ``text=None`` marks an original that
    cannot fit; it consumes no budget, so smaller, older originals can follow.
    An excluded original is not fully read or validated. Without this optional
    budget the original string is returned unchanged, as before.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        raise ValueError("max_bytes must be a nonnegative integer")
    with db() as con:
        # Snapshot only small metadata, never all compressed BLOBs. Closing
        # each read before yielding also lets other sessions write receipts.
        rows = con.execute("SELECT id, timestamp, source, original_chars FROM receipts "
                           "ORDER BY timestamp DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    remaining = max_bytes
    for identifier, stamp, source, characters in rows:
        # Every stored character needs at least one UTF-8 byte. This avoids
        # loading even the compressed BLOB when it is already known not to fit.
        if remaining is not None and characters > remaining:
            yield identifier, stamp, source, None
            continue
        with db() as con:
            row = con.execute("SELECT original FROM receipts WHERE id=?", (identifier,)).fetchone()
        if row is None:
            continue  # An explicitly configured purge may have removed it.
        text, size = _receipt_text(row[0], remaining)
        del row
        if remaining is not None:
            remaining -= size
        yield identifier, stamp, source, text


def count_event(source: str, outcome: str, original: int = 0, visible: int = 0, micros: int = 0) -> None:
    """Add to a daily counter. Holds sizes only, and a counting failure never matters."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        with db() as con:
            con.execute("INSERT INTO counters VALUES (?, ?, ?, 1, ?, ?, ?) "
                        "ON CONFLICT(day, source, outcome) DO UPDATE SET calls=calls+1, "
                        "original_chars=original_chars+excluded.original_chars, "
                        "visible_chars=visible_chars+excluded.visible_chars, "
                        "micros=micros+excluded.micros", (day, source[:60], outcome, original, visible, micros))
    except (OSError, sqlite3.Error, ValueError):
        pass


def report() -> dict:
    with db() as con:
        row = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(original_chars),0), "
            "COALESCE(SUM(visible_chars),0) FROM receipts"
        ).fetchone()
        newest = con.execute("SELECT id, source, original_chars, visible_chars FROM receipts "
                             "ORDER BY timestamp DESC LIMIT 8").fetchall()
        stored = con.execute("SELECT COALESCE(SUM(LENGTH(original)),0) FROM receipts").fetchone()[0]
        events = con.execute("SELECT source, outcome, SUM(calls), SUM(original_chars), SUM(visible_chars), "
                             "SUM(micros) FROM counters GROUP BY source, outcome "
                             "ORDER BY source, outcome").fetchall()
    target = home() / "store.sqlite3"
    return {"receipts": row[0], "original_chars": row[1], "visible_chars": row[2],
            "saved_chars": row[1] - row[2], "recent": newest,
            "stored_bytes": stored, "database_bytes": target.stat().st_size if target.exists() else 0,
            "events": [dict(zip(("source", "outcome", "calls", "original_chars", "visible_chars", "micros"), event))
                       for event in events]}


def purge(*, days: int) -> int:
    if days < 0:
        raise ValueError("days must be nonnegative")
    with db() as con:
        removed = con.execute("DELETE FROM receipts WHERE timestamp < ?",
                              (int(time.time()) - days * 86400,)).rowcount
    if removed:
        # DELETE only marks pages free. Hand the space back to the disk.
        target = home() / "store.sqlite3"
        conn = sqlite3.connect(str(target), timeout=10)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
    return removed


def note_put(scope: str, key: str, value: str) -> None:
    with db() as con:
        con.execute("INSERT INTO notes VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, "
                    "updated=excluded.updated", (scope, key, value, int(time.time())))


def note_get(scope: str, key: str) -> str | None:
    with db() as con:
        row = con.execute("SELECT value FROM notes WHERE scope=? AND key=?", (scope, key)).fetchone()
    return row[0] if row else None


def note_delete(scope: str, key: str) -> bool:
    with db() as con:
        return con.execute("DELETE FROM notes WHERE scope=? AND key=?", (scope, key)).rowcount > 0


def note_list(scope: str, query: str = "", limit: int = 25) -> list[tuple]:
    # LIKE is scoped, parameterized and intentionally simple (no network embeddings).
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db() as con:
        rows = con.execute("SELECT key, value, updated FROM notes WHERE scope=? "
                           "AND (key LIKE ? ESCAPE '\\' OR value LIKE ? ESCAPE '\\') "
                           "ORDER BY updated DESC LIMIT ?", (scope, pattern, pattern, limit)).fetchall()
    return rows
