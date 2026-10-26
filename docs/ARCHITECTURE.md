# Architecture

Stop-Bullshiting is an offline Python application. There is no vendor API, hosted proxy, embedding service or network listener.

## Components

1. `compress.py`: format detection; budgeted log, JSON, diff, CSV and plain-text views. Prioritizes error and summary lines. Returns a content-based preview, not a semantic or lossless compressed representation.
2. `store.py`: local SQLite database at `~/.local/state/stop-bullshiting/store.sqlite3` (or `$SBS_HOME/store.sqlite3`). The database is private mode `0600` on POSIX and its directory mode `0700`. Receipts store the full UTF-8 original compressed with `zlib`; notes are project-scoped and unencrypted.
3. `symbols.py`: AST-aware Python function/class positions, bounded text search with `rg` or Python fallback, exact numbered-line retrieval. No LLM or embedding calls.
4. `hooks.py`: optional Claude `PostToolUse` output replacement and Codex `PreToolUse` allowlisted command transformation, plus reversible installers.
5. `mcp.py`: stdio JSON-RPC MCP entry point implementing `initialize`, `ping`, `tools/list` and `tools/call`.
6. `usage.py`: read-only access to explicit token counters in local Claude Code/Codex log formats. A deliberately separate metric from SBS reduction.
7. `cli.py`: subcommands, safe subprocess capture, banner launcher, diagnostics and benchmarks.

## Data lifecycle

- Tool output arrives either as a terminal command result, stdin stream or hook event.
- Large, recognized output is transformed into a short view; small outputs are left untouched.
- If local storage is enabled, a random receipt ID refers to the complete original in SQLite.
- The reduced view includes a precise restoration command.
- The calling agent can request a full original or exact file excerpt when the compacted view is insufficient.
- `purge` deletes receipts beyond a user-specified age. It does not delete notes.

## Observability and evidence

Observed original and emitted **character counts** are recorded for each receipt. UTF-8 bytes and model tokens are different quantities. Cache token accounting can dominate subscription/API behavior, and model-visible compression can change response quality or trigger follow-up calls. Do not multiply advertised reductions or interpret a synthetic benchmark as actual monetary savings.

For A/B measurement, use the same agent version/model, task fixtures, cache conditions and environment; compare per-session input, output, cached and reasoning tokens (when exposed), pass/fail outcome and latency. Repeat multiple trials before claiming savings.

## Failure strategy

Hooks emit no output if the event schema is unfamiliar, output is too short, or storage fails. Erroring subprocesses emit original stdout/stderr and preserve return code. Stored originals allow debugging any destructive truncation decision. If an integration creates problems, run `sbs setup all --remove` and restore from the `.backup-*` file if needed.
