# Architecture

Stop-Bullshiting's core is an offline Python application. There is no vendor API, hosted proxy, embedding service or network listener. Explicit optional reference-token counting may download a public vocabulary on first use; no input text is uploaded.

## Components

1. `compress.py`: format detection and selective log, JSON and plain-text views. Recognized diagnostic records/lines are kept whole or the original is returned. Auto mode preserves detected code, diffs and CSV; explicit modes allow lossy previews. Python tracebacks pass through in auto/logs/plain. These remain content-based views, not semantic or lossless compression.
2. `store.py`: local SQLite database at `~/.local/state/stop-bullshiting/store.sqlite3` (or `$SBS_HOME/store.sqlite3`). The database is private mode `0600` on POSIX and its directory mode `0700`. Receipts store the full UTF-8 original compressed with `zlib`; notes are project-scoped and unencrypted.
3. `symbols.py`: AST-aware Python function/class positions, bounded text search with `rg` or Python fallback, exact numbered-line retrieval. No LLM or embedding calls.
4. `hooks.py`: optional Claude `PostToolUse` output replacement and Codex `PreToolUse` allowlisted command transformation, plus reversible installers.
5. `mcp.py`: stdio JSON-RPC MCP entry point implementing `initialize`, `ping`, `tools/list` and `tools/call`.
6. `usage.py`: read-only access to explicit token counters in local Claude Code/Codex log formats. A deliberately separate metric from SBS reduction.
7. `cli.py`: subcommands, safe subprocess capture, banner launcher, diagnostics and benchmarks.
8. `receipts.py`: exact numbered ranges and literal-query context from stored originals, with explicit omissions and a bounded response. No second compression pass is applied to selected lines.
9. `metrics.py`: optional tiktoken reference encodings, loaded only when requested. Counts both input and actual preview, keeping negative savings and labeling provider billing as unmeasured.

## Data lifecycle

- Tool output arrives either as a terminal command result, stdin stream or hook event.
- Eligible large output is transformed into a short view; small outputs and protected formats are left untouched. Diagnostic preservation takes priority over the requested view size.
- If local storage is enabled, a random receipt ID refers to the complete original in SQLite.
- The reduced view includes a precise restoration command.
- The calling agent can request a full original, selected receipt lines, literal matches with context or an exact file excerpt when the compacted view is insufficient.
- `purge` deletes receipts beyond a user-specified age. It does not delete notes.

## Observability and evidence

Observed original and emitted **character counts** are recorded for each receipt. UTF-8 bytes and model tokens are different quantities. Cache token accounting can dominate subscription/API behavior, and model-visible compression can change response quality or trigger follow-up calls. Do not multiply advertised reductions or interpret a synthetic benchmark as actual monetary savings.

`sbs bench --tokenizer o200k_base` adds reference-encoding counts for the actual preview and its no-storage marker. It does not include subprocess-wrapper diagnostics, tool protocol envelopes or subsequent restoration. `scripts/benchmark_compression.py` compares real receipt-bearing previews on deterministic synthetic fixtures, checks sentinel presence and exact restoration, and measures local compression time including storage. Neither measures task success or provider billing. See [TOKEN_EFFICIENCY.md](TOKEN_EFFICIENCY.md) for the method and limitations.

For A/B measurement, use the same agent version/model, task fixtures, cache conditions and environment; compare per-session input, output, cached and reasoning tokens (when exposed), pass/fail outcome and latency. Repeat multiple trials before claiming savings.

## Failure strategy

Hooks emit no output if the event schema is unfamiliar, output is too short, or storage fails. Erroring subprocesses emit original stdout/stderr and preserve return code. If local receipt storage or reduction configuration fails after a command succeeds, the CLI also preserves its complete original output and exit code. Stored originals allow debugging a truncation decision. If an integration creates problems, run `sbs setup all --remove` and restore from the `.backup-*` file if needed.
