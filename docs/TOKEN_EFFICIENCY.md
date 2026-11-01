# Token efficiency: changes and evidence

## Direction for SBS

Retrieve only the evidence needed, preserve diagnostic details, and measure the text actually emitted. Aggressive shortening is useful only when it does not cause the agent to miss the answer or fetch the same material again.

SBS keeps its small Python core and local receipts, with no mandatory proxy or inference model.

## Results for version 0.3.0

Measured on **2026-11-01** on a MacBook Air with Apple Silicon, Python 3.14.8, Claude Code 2.1.316 and Codex 0.166.0. Raw data: [measurements](../benchmarks/measurements-2026-11-01.json) and trial records in [`benchmarks/`](../benchmarks/). The baseline is the previous release, commit `1e35c62`.

**In short:** SBS clearly reduces plain, verbose command output, and no task was lost in the final trials. On real sessions whose commands were already chained and windowed it changed almost nothing. Both results are true, and which one applies depends on how the agent works.

### Paired tasks on real Claude Code

Six short tasks on synthetic repositories, with and without the hook, same prompt, fresh repository, no user settings, adjacent pairs in random order. Success is checked against the answer or the repository, never the model's own claim. Reproduce with `scripts/paired_runner.py` and summarize with `scripts/paired_sessions.py`.

| Metric, control → SBS | Haiku 5.5, 30 pairs | Sonnet 5.5, 12 pairs |
|:--|--:|--:|
| Tasks passed | 30/30 → 30/30 | 12/12 → 12/12 |
| New input tokens (input + cache write) | 359,084 → 242,644 (−32%) | 111,077 → 68,960 (−38%) |
| Cache-read tokens | 653,435 → 564,832 (−14%) | 146,915 → 179,362 (+22%) |
| Output tokens | 15,166 → 19,919 (+31%) | 2,873 → 3,692 (+29%) |
| All tokens | 1,027,685 → 827,395 (−19%) | 260,865 → 252,014 (−3%) |
| Cost reported by the client | $0.0859 → $0.0641 (−25%) | $0.4876 → $0.3306 (−32%) |
| Pairs cheaper / dearer with SBS | 28 / 2 | 9 / 3 |
| Tool calls | 66 → 65 | 18 → 22 |
| Wall time | 198 s → 215 s (+9%) | 63 s → 71 s (+13%) |
| Originals fetched back | 0 | 0 |

**Read this as a favorable case.** The tasks were written to exercise the outputs SBS handles and each prompt names the command to run. They are short, the samples are small, and no significance test was run. Models wrote more and took longer with SBS. The cost is the client's own calculation, which is not a provider bill, and a subscription spends quota rather than this amount.

**The first version failed.** It kept head-and-tail previews, and on "find the hash of this commit in `git log`" it passed **0 of 3** against 3 of 3 for the control. The commit sat in omitted lines, and the model answered without fetching them back. In no trial did a model use the restore route. That is why views now keep every commit and match, and why the hook no longer previews: those trials are published [as they ran](../benchmarks/paired-trials-2026-11-01-before-fix.jsonl).

### Replay of real sessions

`scripts/replay_transcripts.py` passes the tool results of the 260 most recent local Claude Code sessions through the hook's policy and prints totals only. It counts characters, not tokens, and it cannot show what a model would then have done.

| Measure | Result |
|:--|--:|
| Bash output replayed | 8,846,175 characters in 3,978 calls |
| Read output replayed | 2,949,328 characters in 949 calls |
| Bash output in chained or piped commands | 90.6% |
| Bash output from a command SBS recognizes | 1.1% |
| Saved by the previous hook | 0 characters; it had stored 0 receipts since being installed |
| Saved by the new hook | 12,550 characters: 0.14% of Bash, 0.11% of everything replayed |
| Diagnostic lines missing from a view | 0 |

These sessions came from one user whose agents window their own output, and another output filter was already active. They do not represent every setup, and they do show the limit of filtering after the fact. An agent running `cd x && a; b | head -40` has already chosen what it wants, and SBS leaves that alone.

### Fidelity fixtures

Characters on synthetic fixtures with `scripts/benchmark_compression.py`. Every fixture kept all its required facts and restored exactly, in both versions unless noted.

| Fixture | Input | Previous | Now | Reduction |
|:--|--:|--:|--:|--:|
| Repetitive logs with an error | 8,416 | 891 | 918 | 89.1% |
| JSON with a failed record in the middle | 17,133 | 578 | 605 | 96.5% |
| Traceback, source and CSV (protected) | 28,081 | 28,081 | 28,081 | 0% |
| pytest -v, passing, one skip and one warning | 19,660 | 19,660 | 312 | 98.4% |
| unittest -v, passing, one skip | 17,257 | 934 | 309 | 98.2% |
| jest, one skip | 8,380 | 701 | 209 | 97.5% |
| Full `git log`, 120 commits | 24,020 | 604 | 16,431 | 31.6% |
| `git status` with 300 untracked files | 7,755 | 758 | 293 | 96.2% |
| `rg` with 500 matches in deep paths | 37,864 | 1,734 | 17,900 | 52.7% |
| `git diff` with a huge lockfile hunk | 43,028 | 43,028 | 404 | 99.1% |
| Colored progress overwritten in place | 25,927 | 661 | 133 | 99.5% |

The `git log` and `rg` views are larger than the previous previews on purpose: those previews fit the size limit by dropping commits and matches, and the paired trials show what that costs.

### Runtime cost

With `scripts/benchmark_runtime.py`, medians over 25 runs, on a machine with other work running, so treat small differences as noise.

| Measure | Previous | Now |
|:--|--:|--:|
| Python startup alone | 13.3 ms | 13.3 ms |
| Hook, small output | 50.9 ms | 29.3 ms |
| Hook, 400-commit log | 56.7 ms | 42.4 ms |
| `sbs compress`, 15 MiB of logs | 1,395 ms | 1,625 ms |
| Peak memory for those 15 MiB | 82 MB | 104 MB |

The hook runs once per Bash or Grep call. The database grew to 0.3 MB in these runs, and `sbs stats` reports its size on your machine.

### Targeted retrieval

With `scripts/benchmark_retrieval.py` on four source trees of 35 to 876 Python and TypeScript files, 150 lookups each.

| Measure | Result |
|:--|--:|
| `sbs symbol`, median size compared with reading the whole file | 1.0% to 6.4% |
| Source returned exactly | 595 of 595; 5 larger symbols were reported with a route to them instead |
| Lookup time, median | 3 to 39 ms |
| `sbs map --ranked`, largest tree | 1.8 s |
| JavaScript/TypeScript declarations against the TypeScript compiler, 3,476 in 360 files | recall 100%, precision 100%, end lines 100% |
| `sbs find` across 200 receipts of 1,500 lines | right passage first in 25 of 25, 564 characters against 62,680 for the whole original |
| `sbs find` time, median | 0.16 s with FTS5, 0.51 s with the Python ranking |

These compare what the tools return. **No trial shows an agent choosing them on its own**, so they are not task savings. The ranked map has no quality evaluation beyond its tests.

### Token counters

The previous `sbs usage` skipped logs over 40 MB, stopped after 1,500 files without saying so and counted a response again in each log repeating it. On the test machine it read 1,500 of 5,134 Claude Code logs and 43,132 of 117,540 responses, and missed 27% of Codex input tokens. Totals will rise after updating, which is a correction rather than higher usage.

### Tried and rejected after measurement

| Idea | Why it was dropped |
|:--|:--|
| Previews of Read results | Claude Code renumbers replaced content. In a real session a model reported line 3 for line 501, for at most 0.55% of Read characters. |
| Replacing repeated outputs with a pointer | It looked like 17% of output, but that was logs repeating results. True repeats were 5 calls and 0.04%. |
| Folding repeats for every command | 0.15% of Bash in the replay, against a stated rule that unrecognized output stays unchanged. Colors and overwritten progress are still removed. |
| Head-and-tail previews in the hook | Lost a task every time it mattered, as above. Available through `SBS_COMPRESS_ALL=1` and `sbs run`. |
| Codex `PostToolUse` with `decision: block` | Removes the exit status from what the model sees, and Codex records an error. |
| A persistent full-text index of receipts | It would keep readable copies of tool output on disk. Building the index per query took 0.16 s. |
| A native parser dependency for JavaScript and TypeScript | The built-in tokenizer matched the TypeScript compiler on the test corpus. |


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

## Version 0.2.0 comparison

This section records version 0.2.0. The local synthetic benchmark compares the clean baseline commit [`ae5fcf4`](https://github.com/searadacunha/Stop-Bullshiting/commit/ae5fcf481284e109d1ac1d92cd56b5292636a628) with the 0.2.0 working changes. Both use auto mode, the same six fixtures, a 3,500-character target, a 1,400-character threshold and minimum character gain of 12%. Python was 3.12.14 and tiktoken was 0.14.0 using `o200k_base`.

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

1. **Trials on real repositories and longer tasks.** The paired trials use short synthetic tasks. Rerun them on real work, on the models people actually use.
2. **Measure whether agents use the retrieval tools.** `sbs symbol` and `sbs find` return far less than a whole file, but no trial yet shows an agent choosing them on its own. Read is about a quarter of replayed tool output, and SBS cannot reduce it after the fact.
3. **Make omitted content easier to fetch back.** Models did not use the restore route once in the trials. Wording, or returning the right passage up front, may matter more than any filter.

No general session-saving figure is claimed.
