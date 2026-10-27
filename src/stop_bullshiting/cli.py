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
from .hooks import claude_post_hook, codex_pre_hook, safe_read_command, setup_agent, setup_shell
from .store import get_receipt, note_delete, note_get, note_list, note_put, purge, report
from .symbols import excerpt, search, symbol_map

MAX_CAPTURE = 16 * 1024 * 1024


def _print_stats(original: int, visible: int) -> None:
    change = 100 * (1 - visible / original) if original else 0
    print(f"Original: {original:,} chars | Visible: {visible:,} chars | "
          f"Reduction: {change:.1f}% (characters, not measured model tokens)", file=sys.stderr)


def _sanitize_args(args: list[str]) -> list[str]:
    return args[1:] if args and args[0] == "--" else args


def _execute(argv: list[str], *, no_store: bool, raw: bool, mode: str, max_len: int) -> int:
    if not argv:
        raise ValueError("Missing command after --")
    if raw:
        return subprocess.call(argv)
    # Write stdout/stderr to temporary files; never buffer unbounded output in RAM.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
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
                                           source="run:" + " ".join(argv[:3]), store=not no_store)
                    except (OSError, sqlite3.Error, ValueError):
                        # The command has already run. A storage/configuration error
                        # must never discard its output or replace its exit status.
                        print("[SBS] Reduction unavailable: preserving original output",
                              file=sys.stderr)
                        sys.stdout.buffer.write(raw_bytes)
                    else:
                        sys.stdout.write(reduced.text)
                        if reduced.receipt:
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
    comp.add_argument("--max-chars", type=int, default=3500)
    comp.add_argument("--min-chars", type=int, default=1400)
    comp.add_argument("--no-store", action="store_true", help="No local reversible receipt")
    comp.add_argument("--stats", action="store_true")
    show = sub.add_parser("show", help="Recover a complete original from its receipt ID")
    show.add_argument("id")
    run = sub.add_parser("run", help="Execute a command and optionally compact its stdout")
    run.add_argument("--mode", choices=["auto", "json", "logs", "diff", "code", "csv", "plain"], default="auto")
    run.add_argument("--max-chars", type=int, default=3500)
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
    symbols = sub.add_parser("map", help="Print accurate Python function/class locations")
    symbols.add_argument("path", nargs="?", default=".")
    symbols.add_argument("--name", default="")
    symbols.add_argument("--limit", type=int, default=160)
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
    sub.add_parser("stats", help="Show locally observed character reduction")
    usage = sub.add_parser("usage", help="Read true token counters from available agent logs")
    usage.add_argument("agent", choices=["claude", "codex", "all"], default="all", nargs="?")
    usage.add_argument("--json", action="store_true")
    clean = sub.add_parser("purge", help="Delete local original receipts older than N days")
    clean.add_argument("--older-than-days", type=int, required=True)
    bench = sub.add_parser("bench", help="Run deterministic local synthetic or file benchmark")
    bench.add_argument("--file", type=Path, default=None)
    bench.add_argument("--mode", default="auto", choices=["auto", "json", "logs", "diff", "code", "csv", "plain"])
    sub.add_parser("mcp", help="Start local JSON-RPC MCP stdio server (no network)")
    sub.add_parser("doctor", help="Inspect optional prerequisites and local state")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "banner":
            banner(stream=sys.stdout)
        elif args.cmd == "compress":
            body = sys.stdin.read()
            item = compress(body, mode=args.mode, max_len=args.max_chars, minimum=args.min_chars,
                            source="stdin", store=not args.no_store)
            sys.stdout.write(item.text)
            if args.stats:
                _print_stats(item.original_chars, item.visible_chars)
        elif args.cmd == "show":
            data = get_receipt(args.id)
            if data is None:
                print(f"Receipt {args.id} not found", file=sys.stderr)
                return 1
            sys.stdout.write(data)
        elif args.cmd == "run":
            return _execute(_sanitize_args(args.argv), no_store=args.no_store,
                            raw=args.raw, mode=args.mode, max_len=args.max_chars)
        elif args.cmd == "shell":
            if not safe_read_command(args.command):
                print("[SBS] Refusing to wrap non-read-only/complex shell expression", file=sys.stderr)
                return 2
            return _execute(shlex.split(args.command), no_store=False, raw=False,
                            mode="auto", max_len=3500)
        elif args.cmd == "launch":
            return _launch(args.agent, args.argv)
        elif args.cmd == "hook":
            raw = sys.stdin.read()
            reply = claude_post_hook(raw) if args.agent == "claude" else codex_pre_hook(raw)
            if reply:
                sys.stdout.write(reply + "\n")
        elif args.cmd == "setup":
            for target in (["claude", "codex", "shell"] if args.target == "all" else [args.target]):
                message = setup_shell(remove=args.remove) if target == "shell" else setup_agent(target, remove=args.remove)
                print(message)
            if not args.remove and args.target in ("shell", "all"):
                print("Open a new zsh terminal for the claude/codex launch banners.")
            if not args.remove and args.target in ("codex", "all"):
                print("In Codex, open /hooks to review and trust the installed hook before it can run.")
        elif args.cmd == "map":
            for line in symbol_map(Path(args.path), name_filter=args.name, limit=args.limit):
                print(line)
        elif args.cmd == "search":
            for line in search(args.query, Path(args.path), limit=args.limit):
                print(line)
        elif args.cmd == "read":
            print(excerpt(Path(args.file), args.start, args.end))
        elif args.cmd == "note":
            return _notes(args)
        elif args.cmd == "stats":
            data = report()
            print(f"Receipts: {data['receipts']} | Original: {data['original_chars']:,} chars | "
                  f"Visible: ~{data['visible_chars']:,} chars | Saved: ~{data['saved_chars']:,} chars")
            print("Character differences only; actual model tokens, cache hits and subscription quotas NOT measured.")
            for ident, source, original, visible in data["recent"]:
                print(f"  {ident} {source[:32]:33} {original:>8} -> ~{visible}")
        elif args.cmd == "usage":
            from .usage import claude_usage, codex_usage
            agents = ([args.agent] if args.agent != "all" else ["claude", "codex"])
            data = [claude_usage() if agent == "claude" else codex_usage() for agent in agents]
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                for record in data:
                    print(f"{record['agent'].upper()}: {record['logs_scanned']} log files")
                    for name, value in record["totals"].items():
                        print(f"  {name:13} {value:,} tokens")
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
            item = compress(text, mode=args.mode, source="benchmark", store=False)
            print(json.dumps({"mode": item.mode, "original_chars": item.original_chars,
                              "visible_chars": item.visible_chars,
                              "char_reduction_pct": round(item.saved_percent, 2),
                              "bench_type": "file" if args.file else "synthetic",
                              "model_tokens_measured": False}, indent=2))
        elif args.cmd == "mcp":
            from .mcp import serve
            return serve()
        elif args.cmd == "doctor":
            print("Stop-Bullshiting v" + __version__)
            print("Python:", sys.version.split()[0])
            print("Local state:", home())
            for binary in ("claude", "codex", "rg", "git"):
                print(f"{binary:8}", shutil.which(binary) or "not found (optional)")
            for path in (Path.home()/".claude"/"settings.json", Path.home()/".codex"/"hooks.json"):
                print(str(path), "present" if path.exists() else "absent")
        return 0
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        print(f"[SBS] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
