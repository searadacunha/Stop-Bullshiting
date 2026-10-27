#!/usr/bin/env python3
"""Offline, synthetic checks of SBS previews, restoration and local overhead.

Run from any directory, without installing SBS:
    python3 scripts/benchmark_compression.py
    python3 scripts/benchmark_compression.py --baseline /path/to/older/checkout
    python3 scripts/benchmark_compression.py --tokenizer o200k_base
    python3 scripts/benchmark_compression.py --explicit-modes

Token counting uses an already installed, locally cached tiktoken encoding.
This script never downloads tokenizer data. Fixtures and receipt IDs are
deterministic; timings naturally vary. Auto detection is the default; explicit
fixture modes are opt-in. No LLM, provider billing, agent success
rate or subscription quota is measured.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import uuid


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    mode: str
    text: str
    sentinels: dict[str, str]
    expect_unchanged: bool = False


def cases() -> list[Case]:
    """Varied fixtures; only the first is predominantly repetitive."""
    logs = ["INFO heartbeat: worker ready"] * 180
    logs += ["ERROR E_SETTLEMENT_417 request=req-b91f amount=-125.50 EUR"]
    logs += [f"INFO completed batch={i:04d} rows={i * 7 + 11}" for i in range(90)]

    records = [{"id": f"request-{i:04d}", "status": "ok", "rows": i * 7 + 13,
                "amount_units": i * 104729, "region": ("eu", "us", "apac")[i % 3],
                "description": f"Synthetic settlement {i} for account acct-{i * 37:06d}"}
               for i in range(90)]
    records[43] = {"id": "tx_b4c291e78a65", "status": "failed",
                   "error": "E_BALANCE_721", "amount_units": -900719925474099312345,
                   "message": "Exact signed integer required; do not round."}

    trace = [f"INFO scanned partition={i:04d} height={2048000 + i * 17} rows={i * 13}"
             for i in range(150)]
    trace[71:71] = [
        "Traceback (most recent call last):",
        '  File "settlement.py", line 137, in reconcile_amount',
        "    validate_exact_units(actual_units, expected_units)",
        "ValueError: E_PRECISION_903 reference=tx_41f9 delta=-0.00000017 BTC",
    ]

    source = ["from decimal import Decimal", ""]
    for i in range(85):
        if i == 42:
            source.extend([
                "def validate_settlement_quote(raw_units, account_id):",
                '    if account_id == "acct_2b4f8109" and raw_units < -900719925474099312345:',
                '        raise ValueError("E_UNIT_FLOOR_508")',
                "    return raw_units", "",
            ])
        source.extend([
            f"def calculate_region_{i:03d}(value, scale={i + 1}):",
            f'    """Synthetic rule for region {i}, threshold {i * 19 + 7}."""',
            f"    adjusted = value * scale + {i * 43}",
            f"    return adjusted if adjusted > {i * 19 + 7} else 0", "",
        ])

    csv_rows = ["record_id,region,status,amount_units,reason"]
    for i in range(130):
        csv_rows.append(
            "csv_9a32ff,eu,failed,-765432109,E_RECONCILE_62" if i == 67 else
            f"record_{i:04d},{('eu', 'us', 'apac')[i % 3]},ok,{i * 97},batch_{i * 11}"
        )

    return [
        Case("repetitive_logs", "Repeated heartbeats plus varied batches and one error", "logs",
             "\n".join(logs) + "\n",
             {"error": "E_SETTLEMENT_417", "request": "req-b91f", "signed_amount": "-125.50 EUR"}),
        Case("json_middle_error", "Unique JSON records with a failing record in the middle", "json",
             json.dumps(records, ensure_ascii=False, indent=2) + "\n",
             {"error": "E_BALANCE_721", "id": "tx_b4c291e78a65",
              "exact_signed_integer": "-900719925474099312345"}),
        Case("traceback", "Distinct scan logs with a traceback and a precise signed amount", "logs",
             "\n".join(trace) + "\n",
             {"error": "E_PRECISION_903", "reference": "tx_41f9",
              "amount_with_unit": "-0.00000017 BTC", "function": "validate_exact_units"}),
        Case("source_identifiers", "Distinct functions; crucial source facts in a middle function", "code",
             "\n".join(source),
             {"function": "validate_settlement_quote", "account": "acct_2b4f8109",
              "comparison": "raw_units < -900719925474099312345", "error": "E_UNIT_FLOOR_508"}),
        Case("csv_middle_error", "Distinct table records with an anomaly away from the edges", "csv",
             "\n".join(csv_rows) + "\n",
             {"id": "csv_9a32ff", "signed_integer": "-765432109", "error": "E_RECONCILE_62"}),
        Case("small_unchanged", "Short Unicode output below the compression threshold", "auto",
             "status=ok\nprocessed=7\namount=-0.25 EUR\nnote=vérifié\n",
             {"status": "status=ok", "signed_amount": "-0.25 EUR", "unicode": "vérifié"}, True),
    ]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tokenizer(name: str):
    metadata = {"requested": name, "available": False, "encoding": None,
                "provider_billing_tokens": False}
    if name == "none":
        metadata["reason"] = "Token counting not requested; character counts are not tokens."
        return None, metadata
    try:
        import tiktoken
        import tiktoken.load
        from importlib.metadata import version

        original_read = tiktoken.load.read_file

        def offline_read(path):
            if "://" in str(path):
                raise RuntimeError("Tokenizer data is not cached locally; downloads are disabled.")
            return original_read(path)

        # read_file_cached reads existing local vocabularies before read_file.
        # Patch only the cache-miss loader, not the actual encoding/counting.
        with patch.object(tiktoken.load, "read_file", side_effect=offline_read):
            encoding = tiktoken.get_encoding(name)
        metadata.update(available=True, encoding=name, package_version=version("tiktoken"),
                        reason="Reference tokenizer count of text only, including the receipt marker.")
        return lambda text: len(encoding.encode(text, disallowed_special=())), metadata
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as exc:
        metadata["reason"] = f"Token counts unavailable offline: {type(exc).__name__}: {exc}"
        return None, metadata


def git_metadata(root: Path) -> dict:
    def read(*args):
        try:
            result = subprocess.run(["git", "-C", str(root), *args], text=True,
                                    capture_output=True, timeout=10, check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    status = read("status", "--porcelain")
    return {"commit": read("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def run_worker(root: Path, args) -> dict:
    source_root = root / "src"
    if not (source_root / "stop_bullshiting" / "compress.py").is_file():
        raise ValueError(f"Not an SBS source checkout: {root}")
    sys.path.insert(0, str(source_root))
    count, token_info = tokenizer(args.tokenizer)
    rows = []
    with tempfile.TemporaryDirectory(prefix="sbs-benchmark-") as storage:
        # Never read or modify a user's receipts or SBS configuration.
        for key in tuple(os.environ):
            if key.startswith("SBS_"):
                del os.environ[key]
        os.environ.update(SBS_HOME=storage, SBS_MIN_GAIN="0.12")
        compressor = importlib.import_module("stop_bullshiting.compress")
        receipts = importlib.import_module("stop_bullshiting.store")
        if not Path(compressor.__file__).resolve().is_relative_to(source_root.resolve()):
            raise RuntimeError("Imported SBS from a different checkout")

        for case in cases():
            requested_mode = case.mode if args.explicit_modes else "auto"
            timings, samples, restorations, sentinel_samples = [], [], [], []
            for iteration in range(args.repeats):
                # Real receipts are still written/read. Stable UUID-shaped IDs
                # remove random marker tokenization noise between checkouts.
                identifier = uuid.uuid5(uuid.NAMESPACE_URL,
                                       f"sbs-synthetic-benchmark-v1/{case.name}/{iteration}")
                captured = io.StringIO()
                with patch.object(receipts.uuid, "uuid4", return_value=identifier), redirect_stdout(captured):
                    started = time.perf_counter_ns()
                    result = compressor.compress(case.text, mode=requested_mode, max_len=args.max_chars,
                                                 minimum=args.min_chars, source="synthetic-benchmark", store=True)
                    timings.append((time.perf_counter_ns() - started) / 1_000_000)
                restored = receipts.get_receipt(result.receipt) if result.receipt else result.text
                restorations.append(restored == case.text)
                sentinel_samples.append({key: value in result.text for key, value in case.sentinels.items()})
                samples.append({"visible_chars": len(result.text),
                                "reference_tokens": count(result.text) if count else None,
                                "visible_sha256": sha256(result.text), "mode": result.mode,
                                "unchanged": result.text == case.text,
                                "has_receipt": result.receipt is not None,
                                "restore_command_visible": (f"sbs show {result.receipt}" in result.text)
                                    if result.receipt else None,
                                "reported_visible_chars_match": result.visible_chars == len(result.text)})
            first = samples[0]
            row = {"name": case.name, "synthetic": True, "description": case.description,
                   "input_sha256": sha256(case.text), "input_chars": len(case.text),
                   "input_reference_tokens": count(case.text) if count else None,
                   "requested_mode": requested_mode, "fixture_explicit_mode": case.mode,
                   "samples": samples,
                   "sentinels": {key: {"literal": value,
                                        "present_in_every_preview": all(sample[key] for sample in sentinel_samples)}
                                 for key, value in case.sentinels.items()},
                   "restoration_exact_every_run": all(restorations),
                   "restoration_method": "stored_receipt" if first["has_receipt"] else "verbatim_output",
                   "unchanged_expected": case.expect_unchanged,
                   "unchanged_expectation_met": all(sample["unchanged"] for sample in samples)
                       if case.expect_unchanged else None,
                   "local_compression_ms": {"samples": timings, "median": statistics.median(timings),
                                            "min": min(timings), "max": max(timings)}}
            rows.append(row)
    return {"source": str(root), "git": git_metadata(root), "tokenizer": token_info,
            "cases": rows}


def run_checkout(root: Path, args) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker-root", str(root),
               "--tokenizer", args.tokenizer, "--repeats", str(args.repeats),
               "--max-chars", str(args.max_chars), "--min-chars", str(args.min_chars)]
    if args.explicit_modes:
        command.append("--explicit-modes")
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Benchmark worker failed for {root}: {(result.stdout or result.stderr)[-2000:]}")
    return json.loads(result.stdout)


def compare(current: dict, baseline: dict) -> list[dict]:
    previous = {case["name"]: case for case in baseline["cases"]}
    comparisons = []
    for case in current["cases"]:
        old = previous[case["name"]]
        if old["input_sha256"] != case["input_sha256"]:
            raise ValueError("Baseline and current fixtures differ")
        now_chars = statistics.mean(sample["visible_chars"] for sample in case["samples"])
        old_chars = statistics.mean(sample["visible_chars"] for sample in old["samples"])
        now_tokens = [sample["reference_tokens"] for sample in case["samples"]]
        old_tokens = [sample["reference_tokens"] for sample in old["samples"]]
        comparable = all(value is not None for value in now_tokens + old_tokens)
        comparisons.append({"name": case["name"], "visible_chars_delta_current_minus_baseline": now_chars - old_chars,
                            "reference_tokens_delta_current_minus_baseline":
                                statistics.mean(now_tokens) - statistics.mean(old_tokens) if comparable else None,
                            "sentinels_newly_preserved": [key for key, value in case["sentinels"].items()
                                if value["present_in_every_preview"] and not old["sentinels"][key]["present_in_every_preview"]],
                            "sentinels_regressed": [key for key, value in case["sentinels"].items()
                                if not value["present_in_every_preview"] and old["sentinels"][key]["present_in_every_preview"]],
                            "restoration_exact_current": case["restoration_exact_every_run"],
                            "restoration_exact_baseline": old["restoration_exact_every_run"]})
    return comparisons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", type=Path, help="Compare the same fixtures against another SBS source checkout")
    parser.add_argument("--tokenizer", choices=("none", "o200k_base", "cl100k_base"), default="none")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=3500)
    parser.add_argument("--min-chars", type=int, default=1400)
    parser.add_argument("--explicit-modes", action="store_true",
                        help="Force each fixture's format instead of testing the default auto policy")
    parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if not 1 <= args.repeats <= 100 or args.max_chars < 300 or args.min_chars < 0:
            raise ValueError("Require 1–100 repeats, max-chars >= 300 and min-chars >= 0")
        if args.worker_root:
            report = run_worker(args.worker_root.expanduser().resolve(), args)
        else:
            root = Path(__file__).resolve().parents[1]
            current = run_checkout(root, args)
            report = {"schema_version": 1, "fixture_set": "synthetic-v1", "python": sys.version.split()[0],
                      "settings": {"max_chars": args.max_chars, "min_chars": args.min_chars,
                                   "min_gain": 0.12, "repeats": args.repeats,
                                   "mode_policy": "explicit" if args.explicit_modes else "auto"},
                      "measurement": {
                          "receipt_ids": "Deterministic UUID5; genuine storage and final rendered markers are measured.",
                          "latency": "Local compress() wall time including receipt storage; excludes imports, token counting and restoration.",
                          "scope": "Synthetic microbenchmark; no agent tasks, remote model calls, billing or quota measured.",
                          "fidelity": "Sentinel presence and exact restoration do not establish semantic equivalence.",
                      }, "current": current}
            if args.baseline:
                baseline = run_checkout(args.baseline.expanduser().resolve(), args)
                report.update(baseline=baseline, comparison=compare(current, baseline))
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
