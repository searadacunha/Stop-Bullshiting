#!/usr/bin/env python3
"""Measure what running SBS costs on this machine for one source checkout.

Hook latency is the whole process as an agent experiences it. Memory is peak RSS
of `sbs compress`. Nothing here touches user state: SBS_HOME is a temporary folder.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def event(stdout: str) -> bytes:
    return json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "git log"},
                       "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}}).encode()


def timed(argv: list[str], data: bytes, env: dict, repeats: int) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        subprocess.run(argv, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False)
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 1)


def peak_rss_mb(argv: list[str], data: bytes, env: dict) -> float:
    code = ("import resource,subprocess,sys;"
            f"subprocess.run({argv!r},input=sys.stdin.buffer.read(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)")
    out = subprocess.run([sys.executable, "-c", code], input=data, capture_output=True, env=env, check=True).stdout
    raw = int(out.strip())
    return round(raw / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def measure(root: Path, repeats: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="sbs-runtime-") as state:
        env = {**os.environ, "SBS_HOME": state, "PYTHONPATH": str(root / "src")}
        python = [sys.executable, "-m", "stop_bullshiting"]
        log = "".join(f"commit {i:040x}\nAuthor: A <a@b.c>\nDate: d\n\n    subject {i}\n\n    body\n\n" for i in range(400))
        huge = ("2026-10-26 12:00:00 INFO worker tick\n" * 440_000)[:15 * 1024 * 1024].encode()
        result = {
            "python_startup_ms": timed([sys.executable, "-c", "pass"], b"", env, repeats),
            "hook_small_output_ms": timed(python + ["hook", "claude"], event("short\n"), env, repeats),
            "hook_large_output_ms": timed(python + ["hook", "claude"], event(log), env, repeats),
            "compress_15mib_ms": timed(python + ["compress"], huge, env, 3),
            "compress_15mib_peak_rss_mb": peak_rss_mb(python + ["compress"], huge, env),
        }
        database = Path(state) / "store.sqlite3"
        result["database_bytes_after_runs"] = database.stat().st_size if database.exists() else 0
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repeats", type=int, default=25)
    args = parser.parse_args()
    print(json.dumps({"python": sys.version.split()[0], **measure(args.root.resolve(), args.repeats)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
