#!/usr/bin/env python3
"""Measure targeted retrieval against the alternative of reading the whole thing.

Characters are what the tools return, not tokens, and not what an agent would do.
Run it on any source tree; nothing in that tree is executed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stop_bullshiting.repomap import ranked_map  # noqa: E402
from stop_bullshiting.symbols import (CODE_SUFFIXES, _iterate, _label, definitions, file_symbols,  # noqa: E402
                                      references, symbol_source)


def code_tree(root: Path, samples: int) -> dict:
    started = time.perf_counter()
    symbols = []
    files = 0
    for file in _iterate(root):
        if file.suffix in CODE_SUFFIXES:
            files += 1
            symbols.extend((file, item) for item in file_symbols(file))
    scan_seconds = time.perf_counter() - started
    names = {}
    for file, item in symbols:
        names.setdefault(item["name"].rsplit(".", 1)[-1], []).append((file, item))
    # A name shared by many declarations is a poor target: real lookups use distinctive ones.
    unique = sorted(name for name, found in names.items() if len(found) == 1 and len(name) > 3)
    chosen = random.Random(7).sample(unique, min(samples, len(unique)))
    ratios, exact, oversized = [], 0, 0
    timings = []
    for name in chosen:
        file, item = names[name][0]
        begin = time.perf_counter()
        text = symbol_source(name, root)
        timings.append(time.perf_counter() - begin)
        with file.open(encoding="utf-8", newline="") as stream:
            whole = stream.read()
        if text.startswith("[SBS:"):
            oversized += 1  # over the response budget: reported with a route to it, not returned
            continue
        wanted = whole.split("\n")[item["line"] - 1:item["end"]]
        shown = [row.split(": ", 1)[1] if ": " in row else "" for row in text.split("\n")[1:]]
        exact += shown == wanted
        ratios.append(len(text) / max(1, len(whole)))
    started = time.perf_counter()
    outline = ranked_map(root, budget=4000)
    map_seconds = time.perf_counter() - started
    total_chars = sum(file.stat().st_size for file in _iterate(root) if file.suffix in CODE_SUFFIXES)
    begin = time.perf_counter()
    if chosen:
        references(chosen[0], root)
    return {"files": files, "symbols": len(symbols), "source_chars": total_chars, "scan_seconds": round(scan_seconds, 2),
            "lookups": len(chosen), "lookup_median_seconds": round(statistics.median(timings), 3) if timings else None,
            "symbol_source_exact": exact, "symbol_over_response_budget": oversized, "symbol_vs_whole_file_chars_median_pct": round(100 * statistics.median(ratios), 1) if ratios else None,
            "ranked_map_seconds": round(map_seconds, 2), "ranked_map_chars": len(outline),
            "references_seconds": round(time.perf_counter() - begin, 2)}


def receipts(count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="sbs-retrieval-") as state:
        os.environ["SBS_HOME"] = state
        from stop_bullshiting.search import find
        from stop_bullshiting.store import get_receipt, save_receipt
        rng = random.Random(11)
        targets = []
        for index in range(count):
            lines = [f"2026-10-26 worker {rng.randrange(9999)} step {rng.randrange(9999)} finished" for _ in range(1500)]
            code = f"E_CODE_{index:04d}"
            lines[rng.randrange(1500)] = f"2026-10-26 ERROR {code} ledger mismatch"
            targets.append((save_receipt("\n".join(lines), source="benchmark", mode="logs", visible_chars=1), code))
        fts, plain, hit, sizes, whole = [], [], 0, [], []
        for identifier, code in rng.sample(targets, min(25, count)):
            started = time.perf_counter()
            result = find(code)
            fts.append(time.perf_counter() - started)
            started = time.perf_counter()
            find(code, use_fts=False)
            plain.append(time.perf_counter() - started)
            best = result["hits"][0] if result["hits"] else {}
            hit += best.get("receipt") == identifier and code in best.get("text", "")
            sizes.append(len(best.get("text", "")))
            whole.append(len(get_receipt(identifier)))
        return {"receipts": count, "lines_each": 1500, "queries": len(fts), "right_passage_first": hit,
                "passage_chars_median": statistics.median(sizes), "whole_original_chars_median": statistics.median(whole),
                "fts5_seconds_median": round(statistics.median(fts), 3), "python_ranking_seconds_median": round(statistics.median(plain), 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", type=Path, default=[ROOT])
    parser.add_argument("--samples", type=int, default=150)
    parser.add_argument("--receipts", type=int, default=200)
    args = parser.parse_args()
    report = {"code": {f"tree_{index}": code_tree(root.resolve(), args.samples) for index, root in enumerate(args.roots, 1)},
              "receipt_search": receipts(args.receipts),
              "scope": "characters returned by tools; not provider tokens or task outcomes"}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
