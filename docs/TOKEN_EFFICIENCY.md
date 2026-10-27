# Token efficiency: comparison, changes and evidence

Reviewed on **2026-10-27**. This is a comparison of approaches relevant to SBS, not a universal ranking or a claim that these projects have been benchmarked head-to-head here.

## Direction for SBS

Retrieve only the evidence needed, preserve diagnostic details, and measure the text actually emitted. Aggressive shortening is useful only when it does not cause the agent to miss the answer or fetch the same material again.

SBS keeps its small Python core and local receipts. Version 0.2.0 adds targeted receipt recovery, more conservative automatic previews, optional reference-token counts and a repeatable regression benchmark. It does not add a mandatory proxy or an inference model.

## Relevant projects

| Project | Useful approach | What SBS takes from the comparison | Boundary |
|:--|:--|:--|:--|
| [RTK](https://github.com/rtk-ai/rtk) | Command-specific filters for common development output, plus measurements of filtered commands | Prefer a parser that understands a command's output over generic truncation; preserve status and diagnostic blocks | Its advertised output reductions are not measured SBS session savings. Native agent file/search tools may bypass shell filters. |
| [Headroom](https://github.com/headroomlabs-ai/headroom) | Structured output compaction and [retrieval of original content](https://docs.headroomlabs.ai/docs/ccr) | Keep error records wherever they occur, then recover a small relevant portion of the original | Original availability and selective retrieval do not prove that every compressed answer is equivalent. Proxy/model features would expand SBS's operating requirements. |
| [Aider](https://github.com/Aider-AI/aider) | A [repository map](https://aider.chat/docs/repomap.html) with definitions and context-relevant selection | Use symbols, search and exact excerpts before reading entire source files | A structural map guides navigation; implementation details still require exact source. SBS's AST map currently supports Python. |
| [Repomix](https://github.com/yamadashy/repomix) | Repository packaging and optional [Tree-sitter structural compression](https://repomix.com/guide/code-compress) | Make source selection deliberate and distinguish structural overviews from exact code | Its experimental compression removes implementation details. That is useful for orientation but insufficient evidence for editing or debugging a function. |
| [Microsoft LLMLingua](https://github.com/microsoft/LLMLingua) | Learned prompt compression, including the token-classification approach in LLMLingua-2 | A possible optional experiment for long prose after task-level evaluation | Model inference, weights, latency and potential information loss need to be included in the comparison. This is not a default dependency for SBS. |

These are independently implemented SBS changes inspired by architectural ideas, not copied competitor filters. Follow each upstream project's license if incorporating its implementation later.

## Why output reduction is not a billing result

An external experiment illustrates the distinction. [JetBrains tested RTK v0.43.0 with Claude Code 2.1.201, claude-sonnet-5 and SkillsBench](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/). It reported a 7.6% median cost increase at low reasoning effort and approximately no change at high effort, despite shorter command output. This finding applies to that experiment and those versions; it is not a result for current RTK or SBS.

For SBS, actual session results depend on how much input passes through its adapters, what the agent subsequently restores, tool-call overhead, model behavior and cache accounting. `sbs stats` records compressed receipts; it does not count every unchanged or bypassed call and cannot establish integration coverage. `sbs usage` reads available provider counters without attributing changes to SBS.

For example, if eligible output were 20% of a session's input and it shrank by 80%, the immediate reduction would be 16% of input before any other effects. Those percentages are an illustration, not measurements of SBS. A cached input token also need not have the same billed cost as a new input token.

## Implemented in 0.2.0

### Recover only the relevant original lines

```bash
sbs show <receipt-id> --find "request-0055" --context 3
sbs show <receipt-id> --start 120 --end 145
```

The same selection is available through `sbs_restore` over MCP using `query`, `start`, `end`, `context` and `limit`. A diagnostic beyond the first 20,000 characters is now reachable directly. Source lines remain complete, with line numbers and explicit omission metadata. Plain `sbs show <receipt-id>` remains an uncapped exact restore.

### Preserve diagnostic evidence

Recognized failed JSON records survive array sampling even when they are in the middle. Parent branches and complete diagnostic values are retained. If their values would change through numeric conversion, or the protected preview cannot fit, the original passes through.

Recognized diagnostic lines in logs and plain text remain complete; a budget too small to hold them produces the original. Python tracebacks pass through in auto/logs/plain modes. Auto mode also leaves detected code, diffs and CSV unchanged. Format detection is heuristic and cannot recognize every language, excerpt or business-critical fact.

Explicit `code`, `diff` and `csv` modes remain deliberately lossy. Use `sbs map`, `sbs search` and `sbs read` to reduce source input through selection. The explicit modes are not a substitute for reading code that will be edited.

### Count a stated reference encoding

```bash
python3 -m pip install -e '.[tokens]'
sbs bench --file sample.log --tokenizer o200k_base
```

The optional counter uses `o200k_base` or `cl100k_base`, counts literal special-token spellings and includes the emitted preview marker. It preserves negative savings if a preview costs more tokens. Missing dependencies or vocabulary-loading failures are explicit errors when counting is requested.

These are reference encodings, not automatically the tokenizer of the user's current agent model. No provider request, billing or quota is measured. Core operation needs no third-party runtime dependency; first explicit use of a tokenizer may download its public vocabulary, without uploading input text.

## Measured comparison

The local synthetic benchmark compares the clean baseline commit [`ae5fcf4`](https://github.com/searadacunha/Stop-Bullshiting/commit/ae5fcf481284e109d1ac1d92cd56b5292636a628) with the 0.2.0 working changes. Both use auto mode, the same six fixtures, a 3,500-character target, a 1,400-character threshold and minimum character gain of 12%. Python was 3.12.14 and tiktoken was 0.14.0 using `o200k_base`.

The table uses the median of three counts for each emitted output. Actual receipt creation is enabled; receipt identifiers are deterministic per fixture and repetition, and the visible restoration command is included. Different identifiers can tokenize differently. The repetitions check consistent behavior on the same inputs; they are not independent agent tasks.

| Synthetic fixture | Original reference tokens | Baseline visible tokens | 0.2.0 visible tokens | 0.2.0 reduction vs original | All fixture sentinels, baseline → 0.2.0 |
|:--|--:|--:|--:|--:|:--|
| Repetitive logs with an error | 1,998 | 258 | 258 | 87.09% | Yes → Yes |
| JSON with a failed record in the middle | 5,515 | 132 | 208 | 96.23% | No → Yes |
| Logs with a middle traceback | 2,380 | 421 | 2,380 | 0% | No → Yes |
| Source with important middle-function details | 4,097 | 916 | 4,097 | 0% | No → Yes |
| CSV with a middle anomaly | 2,168 | 175 | 2,168 | 0% | No → Yes |
| Short Unicode output | 20 | 20 | 20 | 0% | Yes → Yes |

The JSON preview costs more than the baseline preview because it now includes the failed record that was previously missing. The source/CSV/traceback cases deliberately stop compressing in auto mode. Improving correctness can therefore reduce the apparent compression percentage.

All 20 sentinel strings across six fixtures are present in the updated auto-mode outputs. The baseline preserves all required sentinels in only two fixtures. Exact originals are available in both versions, either from stored receipts or unchanged output. These are targeted regression checks, not proof of semantic equivalence or successful real-world debugging.

Forced modes are checked separately with `--explicit-modes`. Source and CSV previews can still lose fixture sentinels in those modes. No aggregate percentage is presented as a representative coding-session saving.

The checked-in [machine-readable summary](../benchmarks/token-efficiency-2026-10-27.json) includes per-fixture hashes, token samples, preservation checks, source identifiers and local latency. Latency includes compression and receipt storage, but excludes imports, tokenizer loading/counting, restoration and subsequent agent work. It is indicative local overhead, not a statistically controlled speed comparison.

The benchmark counts the initial rendered output. Tool envelopes, wrapper diagnostics and any later restoration requests/responses are excluded. Restoring originals to verify them does not make the reported counts a net session measurement.

### Reproduce

```bash
# Character counts and fidelity checks require only the standard library:
python3 scripts/benchmark_compression.py

# After installing the optional extra, explicitly warm the public vocabulary:
sbs bench --tokenizer o200k_base
python3 scripts/benchmark_compression.py --tokenizer o200k_base

# Compare the identical inputs against an existing older checkout:
python3 scripts/benchmark_compression.py --tokenizer o200k_base \
  --baseline /path/to/baseline-checkout

# Keep the deliberately lossy policy separate:
python3 scripts/benchmark_compression.py --tokenizer o200k_base --explicit-modes
```

The script uses temporary receipt storage and isolated subprocesses for each checkout. It never downloads a tokenizer; absent dependencies or uncached vocabularies produce `null` token counts with a reason. It records the commit and whether a checkout contains working changes. Source locations in the archived summary are normalized to avoid embedding machine-specific paths.

## Next improvements, in priority order

1. **Measure actual coverage and recovery overhead.** Count unchanged/bypassed calls as well as compressed calls, and track how often agents retrieve more information. Keep local accounting separate from observed provider counters. The useful target is total work per successful task.
2. **Add a few command-aware filters based on observed workloads.** Test tools such as pytest or Git using real fixture transcripts. Preserve failed test names, diagnostic blocks, file paths and original process status. Avoid changing the original command's semantics just to obtain a shorter result. This does not require broadening hook approval permissions.
3. **Improve task-relevant source navigation.** Rank candidate symbols/files and retrieve exact implementations on demand. Add additional language support only with a deliberate dependency and maintenance decision.
4. **Run paired agent-task trials.** Use the same model, agent version, repository snapshots and task scoring. Randomize order, control/document cache conditions, repeat trials and record input/output/cached/reasoning tokens where available, paid cost, elapsed time, restores and task success. Publish failures as well as gains.
5. **Evaluate learned prose compression only after that baseline exists.** Keep it optional, exclude source/diagnostics by default, and include local inference overhead and answer quality in the decision.

No numeric session-saving target is claimed until those task-level trials exist.
