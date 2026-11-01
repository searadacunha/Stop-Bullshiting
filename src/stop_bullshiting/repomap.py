"""A ranked, character-bounded outline of a repository, computed on demand."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

from .symbols import CODE_SUFFIXES, MAX_FILES, _iterate, _label, file_identifiers, file_symbols


def _identifier_weight(name: str, definers: int) -> float:
    # A name defined everywhere or too short to be distinctive says little about a dependency.
    weight = 1.0 / definers
    if len(name) <= 3 or name.startswith("_"):
        weight *= 0.2
    return weight


def _rank(nodes: list[str], edges: dict[str, dict[str, float]], personal: dict[str, float],
          iterations: int = 25, damping: float = 0.85) -> dict[str, float]:
    count = len(nodes)
    total_personal = sum(personal.values())
    base = {node: (personal.get(node, 0.0) / total_personal if total_personal else 1.0 / count) for node in nodes}
    rank = dict(base)
    out_total = {node: sum(edges[node].values()) for node in nodes if edges.get(node)}
    for _ in range(iterations):
        following = {node: (1 - damping) * base[node] for node in nodes}
        dangling = sum(rank[node] for node in nodes if node not in out_total)
        for node, targets in edges.items():
            share = damping * rank[node] / out_total[node]
            for target, weight in targets.items():
                following[target] += share * weight
        for node in nodes:
            following[node] += damping * dangling * base[node]
        rank = following
    return rank


def ranked_map(root: Path, *, budget: int = 4000, focus: tuple[str, ...] = ()) -> str:
    """List the files other files depend on most, with their most-used symbols first.

    ``focus`` names files or symbols to rank around. Dependencies come from matching
    identifier names, so this orients a search; it is not a call graph.
    """
    root = root.expanduser().resolve()
    symbols: dict[str, list[dict]] = {}
    uses: dict[str, dict[str, list[int]]] = {}
    scanned = 0
    for file in _iterate(root):
        scanned += 1
        if file.suffix not in CODE_SUFFIXES:
            continue
        label = _label(file, root)
        symbols[label] = file_symbols(file)
        uses[label] = file_identifiers(file)
    definers: dict[str, set[str]] = defaultdict(set)
    for label, items in symbols.items():
        for item in items:
            definers[item["name"].rsplit(".", 1)[-1]].add(label)
    nodes = sorted(symbols)
    if not nodes:
        return ""
    edges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    used_by_others: Counter = Counter()
    for label, names in uses.items():
        for name, lines in names.items():
            for target in definers.get(name, ()):
                if target == label:
                    continue
                edges[label][target] += math.sqrt(len(lines)) * _identifier_weight(name, len(definers[name]))
                used_by_others[(target, name)] += len(lines)
    personal: dict[str, float] = {}
    for wanted in focus:
        for label in nodes:
            if wanted in label or wanted in definers and label in definers[wanted]:
                personal[label] = 1.0
    rank = _rank(nodes, edges, personal)

    output: list[str] = []
    used = 0
    shown = 0
    closing = 120
    for label in sorted(nodes, key=lambda node: (-rank[node], node)):
        items = symbols[label]
        if not items:
            continue
        header = label
        if used + len(header) + 1 + closing > budget:
            break
        output.append(header)
        used += len(header) + 1
        shown += 1
        ordered = sorted(items, key=lambda item: (-used_by_others[(label, item["name"].rsplit(".", 1)[-1])], item["line"]))
        chosen = []
        for item in ordered:
            text = item.get("signature") or f"{item['kind']} {item['name']}"
            row = f"  {item['line']}: {text}"
            if used + len(row) + 1 + closing > budget or len(chosen) >= 12:
                break
            chosen.append((item["line"], row))
            used += len(row) + 1
        output.extend(row for _, row in sorted(chosen))
        if len(chosen) < len(items):
            note = f"  ... +{len(items) - len(chosen)} more"
            output.append(note)
            used += len(note) + 1
    with_symbols = sum(1 for node in nodes if symbols[node])
    limit = f"; scan stopped at {MAX_FILES} files" if scanned >= MAX_FILES else ""
    output.append(f"[SBS map: {shown} of {with_symbols} files with symbols{limit}; sbs symbol NAME reads one]")
    return "\n".join(output)
