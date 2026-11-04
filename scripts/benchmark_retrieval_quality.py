#!/usr/bin/env python3
"""Check matching-line fidelity and response budgets on targeted regression fixtures.

No model calls, token counts, billing estimates or production accuracy claims.
Each checkout runs in a separate Python process with a temporary local store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def worker(root: Path) -> dict:
    sys.path.insert(0, str(root / "src"))
    from stop_bullshiting import __version__
    from stop_bullshiting.receipts import select_receipt
    from stop_bullshiting.search import find, render
    from stop_bullshiting.store import get_receipt, save_receipt

    cases = []
    fixtures = (
        ("ordinary_match", ["before\nTARGET complete evidence\nafter\n"], 4096, 512),
        ("match_after_long_context", ["x" * 2000 + "\nTARGET complete evidence\n"], 4096, 512),
        ("older_match_after_oversized_receipt", ["TARGET older evidence\n", "x" * 1001], 1000, 512),
        ("whole_response_budget", [f"TARGET evidence {index}\n" for index in range(5)], 4096, 512),
    )
    for requested_fts in (False, True):
        for name, originals, byte_budget, char_budget in fixtures:
            with tempfile.TemporaryDirectory(prefix="sbs-quality-") as state:
                with patch.dict(os.environ, {"SBS_HOME": state, "SBS_RECEIPT_MAX_MB": "",
                                             "SBS_RECEIPT_RETENTION_DAYS": ""}):
                    ids = [save_receipt(text, source="fixture:" + "long-source-" * 12,
                                        mode="plain", visible_chars=1) for text in originals]
                    stored = dict(zip(ids, originals))
                    result = find("TARGET", limit=5, max_bytes=byte_budget,
                                  max_chars=char_budget, use_fts=requested_fts)
                    text = render(result)
                    exact = True
                    for hit in result["hits"]:
                        rows = hit["text"].split("\n")
                        original_lines = stored[hit["receipt"]].split("\n")
                        numbers = []
                        for row in rows:
                            number, sep, content = row.partition(": ")
                            if not sep or not number.isdecimal():
                                exact = False
                                continue
                            index = int(number) - 1
                            numbers.append(index + 1)
                            exact &= 0 <= index < len(original_lines) and content == original_lines[index]
                        exact &= bool(numbers) and numbers == list(range(hit["start"], hit["end"] + 1))
                    checks = {
                        "matching_line_visible": bool(result["hits"]) and all(
                            "TARGET" in hit["text"] for hit in result["hits"]),
                        "complete_source_lines_and_ranges": exact,
                        "rendered_response_within_budget": len(text) <= char_budget,
                        "originals_restore_exactly": all(get_receipt(ident) == value for ident, value in stored.items()),
                    }
                    cases.append({"name": name, "requested_engine": "fts5" if requested_fts else "python",
                                  "actual_engine": result["engine"], "checks": checks,
                                  "passed": all(checks.values()), "response_chars": len(text),
                                  "response_budget": char_budget, "hits": len(result["hits"]),
                                  "coverage": result.get("coverage")})

    original = "\n".join(["context " + "x" * 180] * 3
                          + ["TARGET important exact evidence"]
                          + ["context " + "y" * 180] * 3)
    text = select_receipt(original, query="TARGET", context=2, max_chars=512)
    checks = {"matching_line_visible": "4: TARGET important exact evidence\n" in text,
              "rendered_response_within_budget": len(text) <= 512}
    cases.append({"name": "literal_restore_prioritizes_match", "checks": checks,
                  "passed": all(checks.values()), "response_chars": len(text), "response_budget": 512})
    modules = ("search.py", "receipts.py", "store.py")
    return {"version": __version__, "sources_sha256": {
        name: hashlib.sha256((root / "src" / "stop_bullshiting" / name).read_bytes()).hexdigest()
        for name in modules}, "passed_cases": sum(case["passed"] for case in cases),
        "total_cases": len(cases), "cases": cases}


def run_checkout(root: Path) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker-root", str(root)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if run.returncode:
        raise RuntimeError(run.stderr.strip() or run.stdout.strip() or "Benchmark worker failed")
    return json.loads(run.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, help="Run the same fixtures on an older source checkout")
    parser.add_argument("--check", action="store_true", help="Fail if a current-checkout fixture fails")
    parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker_root:
            print(json.dumps(worker(args.worker_root.resolve()), indent=2))
            return 0
        current = run_checkout(ROOT)
        report = {"schema_version": 1, "fixture_set": "retrieval-quality-v1",
                  "scope": "Targeted synthetic regression fixtures, not general search accuracy or token savings.",
                  "python": sys.version.split()[0], "current": current}
        if args.baseline:
            report["baseline"] = run_checkout(args.baseline.expanduser().resolve())
        print(json.dumps(report, indent=2, allow_nan=False))
        return int(args.check and current["passed_cases"] != current["total_cases"])
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
