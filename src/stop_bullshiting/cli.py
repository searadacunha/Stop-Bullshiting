"""Command line interface: sbs <subcommand>. Python stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .banner import banner
from .compress import compress
from .config import home
from .hooks import hook_main, safe_read_command, setup_agent, setup_shell
from .metrics import TOKENIZERS, measure_reference_tokens
from .receipts import select_receipt
from .repomap import ranked_map
from .search import find as find_receipts, render as render_receipts
from .store import get_receipt, note_delete, note_get, note_list, note_put, purge, report
from .symbols import definitions, excerpt, references, search, symbol_map, symbol_source

MAX_CAPTURE = 16 * 1024 * 1024


def _print_stats(original: int, visible: int) -> None:
    change = 100 * (1 - visible / original) if original else 0
    print(f"Original: {original:,} chars | Visible: {visible:,} chars | "
          f"Reduction: {change:.1f}% (characters, not measured model tokens)", file=sys.stderr)


def _sanitize_args(args: list[str]) -> list[str]:
    return args[1:] if args and args[0] == "--" else args


def _execute(argv: list[str], *, no_store: bool, raw: bool, mode: str, max_len: int | None, quiet: bool = False) -> int:
    if not argv:
        raise ValueError("Missing command after --")
    if raw:
        return subprocess.call(argv)
    # Write stdout/stderr to temporary files; never buffer unbounded output in RAM.
    try:
        out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    except OSError:
        # A read-only sandbox has no temporary directory. The command must still run.
        return subprocess.call(argv)
    with out, err:
        process = subprocess.run(argv, stdout=out, stderr=err, check=False)
        out.seek(0, os.SEEK_END)
        output_size = out.tell()
        out.seek(0)
        err.seek(0)
        if output_size > MAX_CAPTURE:
            print(f"[SBS] Output larger than {MAX_CAPTURE // (1024 * 1024)} MiB: bypassing reduction",
                  file=sys.stderr)
            shutil.copyfileobj(out, sys.stdout.buffer)
        else:
            raw_bytes = out.read()
            if b"\x00" in raw_bytes or process.returncode != 0:
                sys.stdout.buffer.write(raw_bytes)
            else:
                try:
                    text = raw_bytes.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    # Do not corrupt non-UTF-8 terminal output. No receipt is created.
                    sys.stdout.buffer.write(raw_bytes)
                else:
                    try:
                        reduced = compress(text, mode=mode, max_len=max_len,
                                           source="run:" + " ".join(argv[:3]), store=not no_store,
                                           command=shlex.join(argv))
                    except (OSError, sqlite3.Error, ValueError):
                        # The command has already run. A storage/configuration error
                        # must never discard its output or replace its exit status.
                        print("[SBS] Reduction unavailable: preserving original output",
                              file=sys.stderr)
                        sys.stdout.buffer.write(raw_bytes)
                    else:
                        sys.stdout.write(reduced.text)
                        if reduced.receipt and not quiet:
                            _print_stats(reduced.original_chars, reduced.visible_chars)
        sys.stdout.flush()
        shutil.copyfileobj(err, sys.stderr.buffer)
        sys.stderr.flush()
        return process.returncode


def _launch(agent: str, rest: list[str]) -> int:
    # shutil.which resolves executable paths, not the optional user shell function.
    binary = shutil.which(agent)
    if not binary:
        print(f"[SBS] Executable not found: {agent}", file=sys.stderr)
        return 127
    banner()
    try:
        return subprocess.call([binary] + _sanitize_args(rest))
    except KeyboardInterrupt:
        return 130


def _compress_stdin(args) -> int:
    """Reduce standard input without ever holding more than the capture limit, or losing it."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        body = sys.stdin.read().encode("utf-8")
    else:
        body = stream.read(MAX_CAPTURE + 1)
        if len(body) > MAX_CAPTURE:
            print(f"[SBS] Input larger than {MAX_CAPTURE // (1024 * 1024)} MiB: bypassing reduction", file=sys.stderr)
            sys.stdout.buffer.write(body)
            shutil.copyfileobj(stream, sys.stdout.buffer)
            sys.stdout.flush()
            return 0
    try:
        text = body.decode("utf-8")
        item = compress(text, mode=args.mode, max_len=args.max_chars, minimum=args.min_chars,
                        source="stdin", store=not args.no_store, command=args.command)
    except (OSError, sqlite3.Error, ValueError):
        print("[SBS] Reduction unavailable: preserving original input", file=sys.stderr)
        sys.stdout.flush()
        getattr(sys.stdout, "buffer", None).write(body) if hasattr(sys.stdout, "buffer") else sys.stdout.write(
            body.decode("utf-8", "replace"))
        return 0
    sys.stdout.write(item.text)
    if args.stats:
        _print_stats(item.original_chars, item.visible_chars)
    return 0


def _print_report(data: dict) -> None:
    print(f"Receipts: {data['receipts']} | Original: {data['original_chars']:,} chars | "
          f"Visible: ~{data['visible_chars']:,} chars | Saved: ~{data['saved_chars']:,} chars")
    print(f"Disk: {data['stored_bytes']:,} bytes of compressed originals; "
          f"database file {data['database_bytes']:,} bytes.")
    print("Character differences only; actual model tokens, cache hits and subscription quotas NOT measured.")
    for ident, source, original, visible in data["recent"]:
        print(f"  {ident} {source[:32]:33} {original:>8} -> ~{visible}")
    hooks = [event for event in data["events"] if event["source"].startswith("claude:")]
    if hooks:
        print("Hook coverage (calls seen, chars seen, chars sent, mean hook time inside Python):")
        for event in hooks:
            mean = event["micros"] / event["calls"] / 1000
            print(f"  {event['source']:13} {event['outcome']:13} {event['calls']:>6} {event['original_chars']:>12,} "
                  f"{event['visible_chars']:>12,} {mean:>7.1f} ms")
    other = {event["source"]: event["calls"] for event in data["events"] if event["source"] in ("restore", "find")}
    if other:
        print(f"Originals read back: {other.get('restore', 0)} restores, {other.get('find', 0)} searches.")


def _doctor() -> None:
    print("Stop-Bullshiting v" + __version__)
    print("Python:", sys.version.split()[0])
    print("Local state:", home())
    for binary in ("claude", "codex", "rg", "git"):
        print(f"{binary:8}", shutil.which(binary) or "not found (optional)")
    for path in (Path.home()/".claude"/"settings.json", Path.home()/".codex"/"hooks.json"):
        print(str(path), "present" if path.exists() else "absent")
        try:
            hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks", {}) if path.exists() else {}
        except (OSError, ValueError):
            hooks = {}
        wired = [f"{event} ({group.get('matcher')})" for event, groups in hooks.items() if isinstance(groups, list)
                 for group in groups if isinstance(group, dict) and "stop_bullshiting hook" in json.dumps(group)]
        print("  SBS hook:", ", ".join(wired) if wired else "not installed")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        print("SQLite FTS5: available for sbs find (in memory only)")
    except sqlite3.OperationalError:
        print("SQLite FTS5: unavailable; sbs find uses its Python ranking")
    finally:
        conn.close()
    try:
        data = report()
        print(f"Receipts: {data['receipts']}; database file {data['database_bytes']:,} bytes")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Local store unreadable: {type(exc).__name__}: {exc}")
    print("Hook presence is not proof of reduction: see `sbs stats` for what actually changed.")


def _notes(args) -> int:
    scope = str(Path(args.scope or os.getcwd()).expanduser().resolve())
    if args.note_cmd == "put":
        value = args.text if args.text is not None else sys.stdin.read()
        if not value.strip():
            raise ValueError("Cannot save an empty note")
        note_put(scope, args.key, value)
        print(f"Saved note '{args.key}' locally in scope {scope}")
    elif args.note_cmd == "get":
        value = note_get(scope, args.key)
        if value is None:
            return 1
        print(value)
    elif args.note_cmd == "delete":
        return 0 if note_delete(scope, args.key) else 1
    elif args.note_cmd in ("list", "search"):
        for key, value, _ in note_list(scope, args.query or ""):
            print(f"{key}: {value[:160].replace(chr(10), ' ')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sbs", description="Stop-Bullshiting · Local context optimization")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("banner", help="Show the terminal logo")
    comp = sub.add_parser("compress", help="Compact standard input (full original saved locally)")
    comp.add_argument("--mode", choices=["auto", "json", "logs", "diff", "code", "csv", "plain"], default="auto")
    comp.add_argument("--max-chars", type=int, default=None, help="Size limit (default: SBS_MAX_CHARS or 3500)")
    comp.add_argument("--min-chars", type=int, default=None, help="Leave smaller input alone (default: SBS_MIN_CHARS or 1400)")
    comp.add_argument("--no-store", action="store_true", help="No local reversible receipt")
    comp.add_argument("--stats", action="store_true")
    comp.add_argument("--command", default=None, help="Command that printed this input, to select its view")
    show = sub.add_parser("show", help="Recover an original or select exact lines from a receipt")
    show.add_argument("id")
    show.add_argument("--start", type=int, help="First original line to retrieve (1-based)")
    show.add_argument("--end", type=int, help="Last original line to retrieve (inclusive)")
    show.add_argument("--find", help="Find a case-sensitive literal phrase in the original")
    show.add_argument("--context", type=int, default=2, help="Context lines around each match")
    show.add_argument("--limit", type=int, default=50, help="Maximum source lines to consider; matches precede context")
    run = sub.add_parser("run", help="Execute a command and optionally compact its stdout")
    run.add_argument("--mode", choices=["auto", "json", "logs", "diff", "code", "csv", "plain"], default="auto")
    run.add_argument("--max-chars", type=int, default=None, help="Size limit (default: SBS_MAX_CHARS or 3500)")
    run.add_argument("--no-store", action="store_true")
    run.add_argument("--raw", action="store_true")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    shell = sub.add_parser("shell", help="Internal safe shell wrapper (no arbitrary shell expansion)")
    shell.add_argument("command")
    hook = sub.add_parser("hook", help="Internal JSON hook protocol")
    hook.add_argument("agent", choices=["claude", "codex"])
    setup = sub.add_parser("setup", help="Install an opt-in, reversible hook / shell launcher")
    setup.add_argument("target", choices=["claude", "codex", "shell", "all"])
    setup.add_argument("--remove", action="store_true")
    launch = sub.add_parser("launch", help="Display the banner, then launch the real CLI")
    launch.add_argument("agent", choices=["claude", "codex"])
    launch.add_argument("argv", nargs=argparse.REMAINDER)
    symbols = sub.add_parser("map", help="Print Python, JavaScript and TypeScript symbol locations")
    symbols.add_argument("path", nargs="?", default=".")
    symbols.add_argument("--name", default="")
    symbols.add_argument("--limit", type=int, default=160)
    symbols.add_argument("--ranked", action="store_true", help="Most depended-on files and symbols first")
    symbols.add_argument("--budget", type=int, default=4000, help="Character budget for --ranked")
    symbols.add_argument("--focus", action="append", default=[], help="File or symbol to rank around (repeatable)")
    found = sub.add_parser("find", help="Rank exact passages across stored originals")
    found.add_argument("query")
    found.add_argument("--limit", type=int, default=5)
    found.add_argument("--receipts", type=int, default=200, help="How many recent receipts to search")
    found.add_argument("--max-chars", type=int, default=6000,
                       help="Total text response budget, including headings and notices (minimum: 256)")
    found.add_argument("--json", action="store_true")
    define = sub.add_parser("def", help="Find where a symbol is declared")
    define.add_argument("name")
    define.add_argument("path", nargs="?", default=".")
    refs = sub.add_parser("refs", help="List lines using an identifier (name match, scope not resolved)")
    refs.add_argument("name")
    refs.add_argument("path", nargs="?", default=".")
    refs.add_argument("--limit", type=int, default=100)
    code = sub.add_parser("symbol", help="Print the exact source of a declaration")
    code.add_argument("name")
    code.add_argument("path", nargs="?", default=".")
    grep = sub.add_parser("search", help="Fixed-string search without dumping whole files")
    grep.add_argument("query")
    grep.add_argument("path", nargs="?", default=".")
    grep.add_argument("--limit", type=int, default=50)
    read = sub.add_parser("read", help="Fetch exact numbered source lines")
    read.add_argument("file")
    read.add_argument("start", type=int)
    read.add_argument("end", type=int)
    notes = sub.add_parser("note", help="Per-project local persistent notes")
    notes.add_argument("--scope", default=None)
    note_sub = notes.add_subparsers(dest="note_cmd", required=True)
    put = note_sub.add_parser("put")
    put.add_argument("key")
    put.add_argument("text", nargs="?")
    get = note_sub.add_parser("get")
    get.add_argument("key")
    dele = note_sub.add_parser("delete")
    dele.add_argument("key")
    note_sub.add_parser("list")
    find = note_sub.add_parser("search")
    find.add_argument("query")
    stats = sub.add_parser("stats", help="Show observed character reduction, hook coverage and disk use")
    stats.add_argument("--json", action="store_true")
    usage = sub.add_parser("usage", help="Read true token counters from available agent logs")
    usage.add_argument("agent", choices=["claude", "codex", "all"], default="all", nargs="?")
    usage.add_argument("--json", action="store_true")
    usage.add_argument("--since", help="Only count records from this UTC date on (YYYY-MM-DD)")
    clean = sub.add_parser("purge", help="Delete local original receipts older than N days")
    clean.add_argument("--older-than-days", type=int, required=True)
    bench = sub.add_parser("bench", help="Run deterministic local synthetic or file benchmark")
    bench.add_argument("--file", type=Path, default=None)
    bench.add_argument("--mode", default="auto", choices=["auto", "json", "logs", "diff", "code", "csv", "plain"])
    bench.add_argument("--max-chars", type=int, default=3500)
    bench.add_argument("--tokenizer", choices=TOKENIZERS,
                       help="Optional named reference encoding; requires the tokens extra")
    sub.add_parser("mcp", help="Start local JSON-RPC MCP stdio server (no network)")
    sub.add_parser("doctor", help="Inspect optional prerequisites and local state")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "banner":
            banner(stream=sys.stdout)
        elif args.cmd == "compress":
            return _compress_stdin(args)
        elif args.cmd == "show":
            data = get_receipt(args.id)
            if data is None:
                print(f"Receipt {args.id} not found", file=sys.stderr)
                return 1
            selected = args.start is not None or args.end is not None or args.find is not None
            if selected:
                data = select_receipt(data, start=args.start, end=args.end,
                                      query=args.find, context=args.context, limit=args.limit)
            elif args.context != 2 or args.limit != 50:
                raise ValueError("--context and --limit require --find or a line selection")
            sys.stdout.write(data)
        elif args.cmd == "run":
            return _execute(_sanitize_args(args.argv), no_store=args.no_store,
                            raw=args.raw, mode=args.mode, max_len=args.max_chars)
        elif args.cmd == "shell":
            if not safe_read_command(args.command):
                print("[SBS] Refusing to wrap non-read-only/complex shell expression", file=sys.stderr)
                return 2
            # The agent reads stderr too here, and the preview already states its sizes.
            return _execute(shlex.split(args.command), no_store=False, raw=False,
                            mode="auto", max_len=None, quiet=True)
        elif args.cmd == "launch":
            return _launch(args.agent, args.argv)
        elif args.cmd == "hook":
            return hook_main(args.agent)
        elif args.cmd == "setup":
            for target in (["claude", "codex", "shell"] if args.target == "all" else [args.target]):
                message = setup_shell(remove=args.remove) if target == "shell" else setup_agent(target, remove=args.remove)
                print(message)
            if not args.remove and args.target in ("shell", "all"):
                print("Open a new zsh terminal for the claude/codex launch banners.")
            if not args.remove and args.target in ("codex", "all"):
                print("In Codex, open /hooks to review and trust the installed hook before it can run.")
        elif args.cmd == "map":
            if args.ranked:
                print(ranked_map(Path(args.path), budget=args.budget, focus=tuple(args.focus)))
            else:
                for line in symbol_map(Path(args.path), name_filter=args.name, limit=args.limit):
                    print(line)
        elif args.cmd == "find":
            result = find_receipts(args.query, limit=args.limit, receipts=args.receipts, max_chars=args.max_chars)
            if args.json:
                # The budget applies to the rendered text, not the JSON envelope.
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                view = render_receipts(result)
                sys.stdout.write(view)
                if not view.endswith("\n") and len(view) < args.max_chars:
                    sys.stdout.write("\n")
        elif args.cmd == "def":
            found = definitions(args.name, Path(args.path))
            for item in found:
                print(f"{item['path']}:{item['line']}-{item['end']} {item['kind']} {item['name']}")
            return 0 if found else 1
        elif args.cmd == "refs":
            found = references(args.name, Path(args.path), limit=args.limit)
            if found:
                print("[name matches, scope not resolved]")
                print("\n".join(found))
            return 0 if found else 1
        elif args.cmd == "symbol":
            text = symbol_source(args.name, Path(args.path))
            if text:
                print(text)
            return 0 if text else 1
        elif args.cmd == "search":
            for line in search(args.query, Path(args.path), limit=args.limit):
                print(line)
        elif args.cmd == "read":
            print(excerpt(Path(args.file), args.start, args.end))
        elif args.cmd == "note":
            return _notes(args)
        elif args.cmd == "stats":
            data = report()
            if args.json:
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                _print_report(data)
        elif args.cmd == "usage":
            from .usage import claude_usage, codex_usage
            agents = ([args.agent] if args.agent != "all" else ["claude", "codex"])
            data = [(claude_usage if agent == "claude" else codex_usage)(since=args.since) for agent in agents]
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                for record in data:
                    print(f"{record['agent'].upper()}: {record['logs_scanned']} log files")
                    for name, value in record["totals"].items():
                        print(f"  {name:13} {value:,} tokens")
                    if record["unreadable_logs"]:
                        print(f"  WARNING: {record['unreadable_logs']} log files could not be read")
                print("Provider-recorded counters from available logs, NOT SBS-attributed savings.")
        elif args.cmd == "purge":
            print(f"Deleted {purge(days=args.older_than_days)} stored original receipts")
        elif args.cmd == "bench":
            if args.file:
                text = args.file.read_text(encoding="utf-8")
            else:
                text = "\n".join(["2026-10-26 INFO worker completed check successfully"] * 250 +
                                 ["2026-10-26 WARNING retrying operation"] * 7 +
                                 ["2026-10-26 ERROR failed to parse row 44"] +
                                 ["2026-10-26 INFO summary: 250 tasks completed"])
            item = compress(text, mode=args.mode, max_len=args.max_chars,
                            source="benchmark", store=False)
            measured = {"mode": item.mode, "original_chars": item.original_chars,
                        "visible_chars": item.visible_chars,
                        "char_reduction_pct": round(item.saved_percent, 2),
                        "bench_type": "file" if args.file else "synthetic",
                        "measurement_scope": "single_output_preview",
                        "preview_marker_included": True,
                        "wrapper_diagnostics_included": False,
                        "receipt_stored": False,
                        "model_tokens_measured": False}
            if args.tokenizer:
                measured["reference_token_counts"] = measure_reference_tokens(
                    text, item.text, args.tokenizer)
            print(json.dumps(measured, indent=2))
        elif args.cmd == "mcp":
            from .mcp import serve
            return serve()
        elif args.cmd == "doctor":
            _doctor()
        return 0
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        print(f"[SBS] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
