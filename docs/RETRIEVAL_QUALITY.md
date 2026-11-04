# Retrieval quality

SBS returns small, inspectable passages from locally stored originals. A useful passage must include the evidence that caused it to match, retain correct source locations, and respect the response budget. Making output shorter alone does not establish any of those properties.

## Design references

The following upstream projects were reviewed on 2026-11-04. They address overlapping problems with different architectures; their published reduction figures are not measurements of SBS.

| Project | Relevant mechanism | Decision for SBS |
| --- | --- | --- |
| [RTK](https://github.com/rtk-ai/rtk) and its [history diagnostics](https://github.com/rtk-ai/rtk/blob/develop/docs/usage/FEATURES.md) | Command-specific filters and analysis of commands missed by integration | SBS already has command-aware filters. A future history diagnostic should be driven by observed workloads, not an arbitrary count of supported commands. |
| [context-mode](https://github.com/mksglu/context-mode) and its [search implementation](https://github.com/mksglu/context-mode/blob/main/src/core/tools/search.ts) | Retrieval of relevant passages with bounded output and explicit continuation | Prioritize matching lines, bound the complete response, and report omitted results. Keep SBS's temporary in-memory index. |
| [Aider repository map](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py) | Ranked code outlines, task focus and cached analysis | SBS already supports ranked maps and focus. Additional caching needs a measured benefit and reliable invalidation. |
| [Repomix](https://github.com/yamadashy/repomix) | Structural code extraction for repository context | Keep targeted symbol retrieval and exact originals. Adding a new parser runtime is outside this retrieval improvement. |
| [Serena](https://github.com/oraios/serena) | Symbol and reference retrieval backed by language tooling | Keep SBS's dependency-free name-based references explicitly labeled; semantic resolution would need a separate optional integration. |

These are design comparisons. No third-party source code or mandatory runtime dependency was added.

## Response contract

`sbs find QUERY --max-chars N` and MCP `sbs_find` return complete numbered source lines. Matching lines receive space before neighboring context. Displayed ranges describe the displayed lines. If a ranked passage cannot include any complete matching line within the remaining budget, it is reported as omitted, with a receipt and range for recovery. A selected passage may leave out other matching lines; search returns ranked excerpts, not an exhaustive match list.

The text budget includes headings, separators and omission/coverage notices. It defaults to 6,000 characters and has a minimum of 256. MCP additionally limits it to 20,000. Structured JSON includes metadata beyond this text budget; its serialized size is not promised to fit within `N`.

`sbs show RECEIPT --find PHRASE` and MCP `sbs_restore` apply the same priority to literal matches, then render retained lines in source order. Full `sbs show RECEIPT` remains available for the unchanged original.

## Search coverage and storage

The default search considers up to 200 recent receipts and admits at most 64 MiB of uncompressed UTF-8 input. These are explicit search limits, not an assertion that every stored log has been searched. An oversized receipt is skipped and does not prevent smaller, older receipts from being considered. JSON results expose coverage and omission metadata; text reports relevant limits and omissions.

Compressed originals are fetched one at a time. Bounded decompression prevents an oversized original from being fully expanded before its search budget is checked. SQLite read cursors are closed before yielding, so a suspended iterator does not retain that cursor's read lock. The input byte budget is not a strict process-memory limit: Python objects, chunk lists and SQLite indexing add overhead.

FTS5 and the Python fallback use different tokenization/ranking implementations. The tests cover both; ranking equivalence for arbitrary queries is not claimed. Search indexes remain ephemeral and no persistent plaintext index is created.

## Reproduce the targeted checks

From a checkout:

```bash
python3 scripts/benchmark_retrieval_quality.py --check
python3 -m unittest discover -s tests -v
```

To compare exactly the same fixtures with the preceding source tree:

```bash
git worktree add --detach ../sbs-before-retrieval 0c07a41d634d6ad721fd172271d55282aa6e6741
python3 scripts/benchmark_retrieval_quality.py --baseline ../sbs-before-retrieval --check
```

Each version runs in its own Python process and temporary receipt store. The report records source hashes, the requested and actual search engine, whether the matching line is visible, whether lines and ranges remain exact, the entire rendered response length, and exact restoration of originals. Fixtures include an ordinary lookup, a match after a long context line, a small older receipt behind an oversized recent one, response-budget accounting across several hits, and literal restoration with long context.

These are targeted regression fixtures. Their pass rate is not a benchmark of general search accuracy, model task success, monetary savings or provider token consumption.

## Recorded comparison

The [2026-11-04 report](../benchmarks/retrieval-quality-2026-11-04.json) runs the same nine cases against the preceding source tree and 0.3.1. Both the FTS5 and Python engines are exercised.

| Targeted fixture | Previous source tree | 0.3.1 |
| --- | --- | --- |
| Ordinary lookup, each engine | Complete matching line; within budget | Same behavior |
| Long context before a match, each engine | Match absent from displayed text | Complete matching line displayed |
| Small older receipt after an oversized recent one, each engine | Older match not reached | Older match displayed; skipped receipt counted |
| Several hits with a 512-character total budget, each engine | 1,018 characters returned | 504 characters returned |
| Literal restoration with long surrounding context | Only context displayed | Matching line displayed first in budget allocation |

The previous tree passes 2 of these 9 deliberately targeted cases; 0.3.1 passes all 9. Every original in both versions restores exactly. This establishes the specified fixes on these fixtures, not a general accuracy percentage or a percentage reduction in agent bills.
