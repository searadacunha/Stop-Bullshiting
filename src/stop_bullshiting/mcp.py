"""Minimal MCP stdio server built with Python's standard library.

Supports initialize/ping/tools/list/tools/call and notifications/initialized.
No HTTP listener, subprocess execution or external model calls.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .compress import compress
from .receipts import select_receipt
from .store import get_receipt, note_get, note_list, note_put
from .symbols import excerpt, search, symbol_map

SERVER = "stop-bullshiting"
PROTOCOL = "2025-03-26"
MAX_MESSAGE = 2 * 1024 * 1024


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": required or [],
        "additionalProperties": False}}


TOOLS = [
    _tool("sbs_compact", "Compact a long text; recover original with sbs_restore. Character savings are not actual token metrics.",
          {"text": {"type": "string"}, "mode": {"type": "string", "enum": ["auto", "json", "logs", "diff", "code", "csv", "plain"]},
           "max_chars": {"type": "integer", "minimum": 300, "maximum": 20000}}, ["text"]),
    _tool("sbs_restore", "Recover a stored original, or exact numbered lines using a range or literal query. Responses are capped at 20000 characters; narrow the selection for large originals.",
          {"receipt": {"type": "string"},
           "start": {"type": "integer", "minimum": 1, "description": "First line, inclusive (1-based)."},
           "end": {"type": "integer", "minimum": 1, "description": "Last line, inclusive."},
           "query": {"type": "string", "minLength": 1, "description": "Case-sensitive literal, not a regular expression."},
           "context": {"type": "integer", "minimum": 0, "default": 2,
                       "description": "Neighboring lines around each match, clipped to the requested range."},
           "limit": {"type": "integer", "minimum": 1, "default": 50,
                     "description": "Maximum selected source lines to consider, including oversized lines."}}, ["receipt"]),
    _tool("sbs_symbols", "Locate Python classes, functions and precise line ranges without reading entire files.",
          {"path": {"type": "string"}, "name": {"type": "string"}}, []),
    _tool("sbs_search", "Search files for a literal phrase and return bounded matching lines.",
          {"query": {"type": "string"}, "path": {"type": "string"}}, ["query"]),
    _tool("sbs_read", "Read exact numbered lines without altering source code.",
          {"file": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
          ["file", "start", "end"]),
    _tool("sbs_note_save", "Store a per-project note in local private SQLite storage. Avoid secrets.",
          {"key": {"type": "string"}, "text": {"type": "string"}, "scope": {"type": "string"}}, ["key", "text"]),
    _tool("sbs_note_find", "Find stored notes for a project, without loading the full history.",
          {"query": {"type": "string"}, "scope": {"type": "string"}}, []),
]


def _text(value: str, *, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": value}], "isError": error}


def invoke(name: str, args: dict) -> dict:
    try:
        if name == "sbs_compact":
            if not isinstance(args["text"], str):
                raise ValueError("text must be a string")
            result = compress(args["text"], mode=args.get("mode", "auto"),
                              max_len=args.get("max_chars", 3500), source="mcp")
            return _text(result.text)
        if name == "sbs_restore":
            data = get_receipt(args["receipt"])
            if data is None:
                raise ValueError("Unknown receipt ID")
            selection = {key: args[key] for key in ("start", "end", "query", "context", "limit") if key in args}
            # Small unqualified restores remain byte-for-byte compatible. Large
            # originals use complete lines plus an explicit route to later data.
            if selection or len(data) > 20000:
                return _text(select_receipt(data, **selection, max_chars=20000))
            return _text(data)
        if name == "sbs_symbols":
            return _text("\n".join(symbol_map(Path(args.get("path", ".")),
                                               name_filter=args.get("name", ""))))
        if name == "sbs_search":
            return _text("\n".join(search(args["query"], Path(args.get("path", ".")))))
        if name == "sbs_read":
            return _text(excerpt(Path(args["file"]), args["start"], args["end"]))
        if name == "sbs_note_save":
            scope = str(Path(args.get("scope", ".")).resolve())
            note_put(scope, args["key"], args["text"])
            return _text("Note saved locally")
        if name == "sbs_note_find":
            scope = str(Path(args.get("scope", ".")).resolve())
            entries = note_list(scope, args.get("query", ""))
            return _text("\n".join(f"{key}: {value[:500]}" for key, value, _ in entries))
        return _text(f"Unknown tool {name}", error=True)
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
        return _text(f"{type(exc).__name__}: {exc}", error=True)


def dispatch(request: dict) -> dict | None:
    method = request.get("method")
    if method and method.startswith("notifications/"):
        return None
    if "id" not in request:
        return None
    ident = request["id"]
    try:
        if method == "initialize":
            params = request.get("params") or {}
            # Protocol versions are negotiated, not ignored.
            requested = params.get("protocolVersion", PROTOCOL)
            version = requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25") else PROTOCOL
            result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": SERVER, "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = request.get("params") or {}
            result = invoke(params.get("name", ""), params.get("arguments") or {})
        else:
            raise LookupError(f"Unknown method {method}")
        return {"jsonrpc": "2.0", "id": ident, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": str(exc)}}


def serve() -> int:
    """Pure newline-delimited JSON-RPC. Keep stdout protocol-only."""
    for raw in sys.stdin.buffer:
        if len(raw) > MAX_MESSAGE:
            sys.stderr.write("[SBS MCP] Oversized message discarded\n")
            continue
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                continue
            response = dispatch(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except (ValueError, UnicodeError) as exc:
            sys.stderr.write(f"[SBS MCP] Invalid JSON input: {exc}\n")
    return 0
