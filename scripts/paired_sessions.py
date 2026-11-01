#!/usr/bin/env python3
"""Summarize paired, user-recorded agent task runs; never invent missing telemetry.

This tool does not run models, proxy API calls, or estimate token/cost fields.
Trial execution, equivalence of inputs and correctness remain external evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

VARIANTS = ("control", "sbs")
METRICS = ("tokens_total", "cost_usd", "tool_calls", "restore_calls",
           "elapsed_seconds", "errors")
COUNTS = {"tokens_total", "tool_calls", "restore_calls", "errors"}


def read_trials(path: Path) -> list[dict]:
    """Validate JSONL records with partial telemetry, never substituting zero."""
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {number}: expected an object")
            if not isinstance(row.get("task_id"), str) or not row["task_id"].strip():
                raise ValueError(f"line {number}: task_id must be a nonempty string")
            if type(row.get("repeat")) is not int or row["repeat"] < 0:
                raise ValueError(f"line {number}: repeat must be a nonnegative integer")
            if row.get("variant") not in VARIANTS:
                raise ValueError(f"line {number}: variant must be control or sbs")
            if type(row.get("success")) is not bool:
                raise ValueError(f"line {number}: success must be a boolean")
            for metric in METRICS:
                val = row.get(metric)
                if val is None:
                    continue
                if (type(val) not in (int, float) or not math.isfinite(val)
                        or val < 0 or (metric in COUNTS and type(val) is not int)):
                    raise ValueError(f"line {number}: invalid {metric}")
            rows.append(row)
    if not rows:
        raise ValueError("No trial records")
    return rows


def summarize(rows: list[dict]) -> dict:
    indexed: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        key = row["task_id"], row["repeat"]
        condition = row["variant"]
        if condition in indexed[key]:
            raise ValueError(f"Duplicate trial: {key}, {condition}")
        indexed[key][condition] = row
    pairs = []
    unmatched = []
    for key, trials in sorted(indexed.items()):
        if set(trials) != set(VARIANTS):
            unmatched.append({"task_id": key[0], "repeat": key[1],
                              "present": sorted(trials)})
            continue
        control, sbs = trials["control"], trials["sbs"]
        # A direct comparison is invalid if the supplied model/input hashes differ.
        for field in ("model", "task_revision", "repository_sha"):
            if control.get(field) and sbs.get(field) and control[field] != sbs[field]:
                raise ValueError(f"Non-equivalent pair {key}: {field} differs")
        pairs.append((key, control, sbs))
    if not pairs:
        raise ValueError("No complete control/SBS pairs")

    metrics = {}
    for field in METRICS:
        available = [(c[field], s[field]) for _, c, s in pairs
                     if c.get(field) is not None and s.get(field) is not None]
        differences = [s - c for c, s in available]
        fractions = [100 * (s - c) / c for c, s in available if c != 0]
        metrics[field] = {
            "pairs_measured": len(available),
            "mean_control": statistics.mean(v[0] for v in available) if available else None,
            "mean_sbs": statistics.mean(v[1] for v in available) if available else None,
            "mean_delta_sbs_minus_control": statistics.mean(differences) if differences else None,
            "median_delta_sbs_minus_control": statistics.median(differences) if differences else None,
            "median_percent_delta_when_control_nonzero": statistics.median(fractions) if fractions else None,
        }
    return {
        "evidence_scope": ("Supplied trial records only. No provider token, billing, "
                           "model/task equality or task correctness was independently verified."),
        "complete_pairs": len(pairs),
        "unmatched_trials": unmatched,
        "pairs_missing_comparability_metadata": sum(
            not all(c.get(k) and s.get(k) for k in ("model", "task_revision", "repository_sha"))
            for _, c, s in pairs),
        "success": {
            "control": sum(c["success"] for _, c, _ in pairs),
            "sbs": sum(s["success"] for _, _, s in pairs),
            "n_each": len(pairs),
        },
        "metrics": metrics,
        "outcomes": [
            {"task_id": key[0], "repeat": key[1],
             "control_success": c["success"], "sbs_success": s["success"]}
            for key, c, s in pairs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trials", type=Path, help="Recorded paired trial JSONL")
    parser.add_argument("--output", type=Path, help="Optional report path")
    args = parser.parse_args()
    result = json.dumps(summarize(read_trials(args.trials)), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
