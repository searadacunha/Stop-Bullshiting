"""Minimal MCP stdio server built with Python's standard library.

Supports initialize/ping/tools/list/tools/call and notifications/initialized.
No HTTP listener, subprocess execution or external model calls.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .compress import compress
from .receipts import select_receipt
from .repomap import ranked_map
from .search import find as find_receipts, render as render_receipts
from .store import get_receipt, note_get, note_list, note_put
from .symbols import definitions, excerpt, references, search, symbol_map, symbol_source

SERVER = "stop-bullshiting"
PROTOCOL = "2025-11-25"
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MAX_MESSAGE = 2 * 1024 * 1024
READ_ONLY = {"sbs_restore", "sbs_symbols", "sbs_search", "sbs_read", "sbs_note_find", "sbs_find",
             "sbs_definition", "sbs_references", "sbs_symbol_source", "sbs_repo_map"}


def _workspace_path(raw: str) -> Path:
    """Restrict MCP file and note operations to a single resolved workspace.

    The trusted MCP host may set SBS_WORKSPACE. Otherwise the server's current
    working directory is the boundary. resolve() checks absolute paths, '..'
    and symlink targets before a read; no path outside it is authorized.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("A workspace-relative path is required")
    try:
        root = Path(os.environ.get("SBS_WORKSPACE") or os.getcwd()).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("SBS_WORKSPACE must be a directory")
        target = (root / Path(raw).expanduser()).resolve()
    except RuntimeError as exc:
        raise ValueError("Cannot resolve workspace path") from exc
    if not target.is_relative_to(root):
        raise ValueError("Path outside MCP workspace")
    return target


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": required or [],
        "additionalProperties": False}, "annotations": {"readOnlyHint": name in READ_ONLY}}


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
    _tool("sbs_find", "Rank complete matching lines across recent originals. The text budget includes headings and notices; skipped originals and omitted passages are reported.",
          {"query": {"type": "string", "minLength": 1},
           "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
           "max_chars": {"type": "integer", "minimum": 256, "maximum": 20000, "default": 6000}}, ["query"]),
    _tool("sbs_symbols", "Locate Python, JavaScript and TypeScript declarations with precise line ranges without reading entire files.",
          {"path": {"type": "string"}, "name": {"type": "string"}}, []),
    _tool("sbs_definition", "Find where a Python, JavaScript or TypeScript symbol is declared.",
          {"name": {"type": "string", "minLength": 1}, "path": {"type": "string"}}, ["name"]),
    _tool("sbs_references", "List lines using an identifier. Matches by name without resolving scope, so verify each one.",
          {"name": {"type": "string", "minLength": 1}, "path": {"type": "string"}}, ["name"]),
    _tool("sbs_symbol_source", "Return the exact numbered source of a declaration without reading its whole file.",
          {"name": {"type": "string", "minLength": 1}, "path": {"type": "string"}}, ["name"]),
    _tool("sbs_repo_map", "Outline the most depended-on files and symbols within a character budget.",
          {"path": {"type": "string"}, "budget": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 4000},
           "focus": {"type": "array", "items": {"type": "string"}}}, []),
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
        if name == "sbs_find":
            limit = args.get("limit", 5)
            max_chars = args.get("max_chars", 6000)
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError("limit must be an integer from 1 to 20")
            if type(max_chars) is not int or not 256 <= max_chars <= 20000:
                raise ValueError("max_chars must be an integer from 256 to 20000")
            return _text(render_receipts(find_receipts(args["query"], limit=limit, max_chars=max_chars)))
        if name == "sbs_definition":
            found = definitions(args["name"], _workspace_path(args.get("path", ".")))
            return _text("\n".join(f"{item['path']}:{item['line']}-{item['end']} {item['kind']} {item['name']}"
                                   for item in found))
        if name == "sbs_references":
            found = references(args["name"], _workspace_path(args.get("path", ".")))
            return _text("[name matches, scope not resolved]\n" + "\n".join(found) if found else "")
        if name == "sbs_symbol_source":
            return _text(symbol_source(args["name"], _workspace_path(args.get("path", "."))))
        if name == "sbs_repo_map":
            budget = args.get("budget", 4000)
            focus = args.get("focus", [])
            if type(budget) is not int or not 200 <= budget <= 20000:
                raise ValueError("budget must be an integer from 200 to 20000")
            if not isinstance(focus, list) or not all(isinstance(item, str) for item in focus):
                raise ValueError("focus must be a list of strings")
            return _text(ranked_map(_workspace_path(args.get("path", ".")), budget=budget, focus=tuple(focus)))
        if name == "sbs_symbols":
            return _text("\n".join(symbol_map(_workspace_path(args.get("path", ".")),
                                               name_filter=args.get("name", ""))))
        if name == "sbs_search":
            return _text("\n".join(search(args["query"], _workspace_path(args.get("path", ".")))))
        if name == "sbs_read":
            return _text(excerpt(_workspace_path(args["file"]), args["start"], args["end"]))
        if name == "sbs_note_save":
            scope = str(_workspace_path(args.get("scope", ".")))
            note_put(scope, args["key"], args["text"])
            return _text("Note saved locally")
        if name == "sbs_note_find":
            scope = str(_workspace_path(args.get("scope", ".")))
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
            version = requested if requested in VERSIONS else PROTOCOL
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
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"Unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": ident, "result": result}
    except (TypeError, ValueError, AttributeError) as exc:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": str(exc)}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": str(exc)}}


def serve() -> int:
    """Pure newline-delimited JSON-RPC. Keep stdout protocol-only."""
    stream = sys.stdin.buffer
    while True:
        # A plain "for raw in stream" allocates the ENTIRE newline-delimited
        # message before checking its length. Bound the read, then drain an
        # oversized frame in fixed-size chunks without storing its contents.
        raw = stream.readline(MAX_MESSAGE + 1)
        if not raw:
            break
        if len(raw) > MAX_MESSAGE:
            if not raw.endswith(b"\n"):
                while True:
                    tail = stream.readline(65536)
                    if not tail or tail.endswith(b"\n"):
                        break
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
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0
