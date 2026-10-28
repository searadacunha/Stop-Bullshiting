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
"""


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
    encoded = text.encode("utf-8", errors="replace")
    with db() as con:
        con.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (identifier, int(time.time()), source[:160], mode, sqlite3.Binary(zlib.compress(encoded, 6)),
             len(text), visible_chars, hashlib.sha256(encoded).hexdigest()),
        )
    return identifier


def get_receipt(identifier: str) -> str | None:
    with db() as con:
        row = con.execute("SELECT original FROM receipts WHERE id = ?", (identifier,)).fetchone()
    return zlib.decompress(row[0]).decode("utf-8") if row else None


def report() -> dict:
    with db() as con:
        row = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(original_chars),0), "
            "COALESCE(SUM(visible_chars),0) FROM receipts"
        ).fetchone()
        newest = con.execute("SELECT id, source, original_chars, visible_chars FROM receipts "
                             "ORDER BY timestamp DESC LIMIT 8").fetchall()
    return {"receipts": row[0], "original_chars": row[1], "visible_chars": row[2],
            "saved_chars": row[1] - row[2], "recent": newest}


def purge(*, days: int) -> int:
    if days < 0:
        raise ValueError("days must be nonnegative")
    with db() as con:
        cursor = con.execute("DELETE FROM receipts WHERE timestamp < ?",
                             (int(time.time()) - days * 86400,))
        return cursor.rowcount


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
