# Changelog

## 0.3.1 — 2026-11-04

### Retrieval

- Allocate space to matching lines before surrounding context in ranked receipt search and literal restoration; preserve complete source lines and accurate line ranges.
- Bound the entire rendered `sbs find` response, including headings and notices. Add CLI `--max-chars` and the optional MCP `sbs_find.max_chars` argument; existing calls retain their defaults.
- Report search coverage and passages omitted by the response budget. A matching line too large to show is identified for recovery instead of silently truncated.
- Continue searching smaller, older originals when a recent receipt exceeds the UTF-8 input budget.
- Read original blobs individually and bound decompression for search, without keeping a SQLite read cursor open while results are yielded.
- Add targeted regression tests and a reproducible comparison script, `scripts/benchmark_retrieval_quality.py`.
- Document the design comparison with RTK, context-mode, Aider, Repomix and Serena in [RETRIEVAL_QUALITY.md](docs/RETRIEVAL_QUALITY.md).

## 0.3.0 — 2026-11-01

### Output

- Add views for `git status/log/diff/show`, pytest, unittest, jest, vitest, go and cargo tests, `rg` and `grep`. They keep every commit, match and diagnostic and remove only redundant text, up to 30,000 characters.
- Recognize the producing command after `cd`, environment assignments and known wrappers, and through `head` or `tail`.
- Remove terminal colors and overwritten progress from any command's output; the original stays recoverable.
- Collapse log lines that differ only by their leading timestamp.
- Protect more diagnostics in generic previews, such as `TypeError:`, `FAIL`, `npm ERR!` and `Segmentation fault`.
- Never let a marker be cut from a view, and return the original when a view plus its marker does not save enough.

### Hooks

- Claude: also handle Grep results, and apply views to chained commands. Add `Bash|Grep` as the matcher.
- Claude: stop previewing allowlisted commands by head and tail. In paired trials models did not fetch omitted lines back and answered wrongly. `SBS_COMPRESS_ALL=1` still allows it.
- Read results are deliberately left alone: Claude Code renumbers replaced content, and a model then reported wrong line numbers.
- **Fix:** the Codex wrapper hid commands from exec-policy rules, so a `forbidden` rule did not fire. A command named by any rule is now left to Codex.
- **Fix:** in a read-only Codex sandbox the wrapper failed for lack of a temporary directory and the command never ran. It now runs the command directly.
- **Fix:** a hook could exit 2 on undecodable input, which blocks a tool or replaces its result. Hooks now always exit 0.
- **Fix:** the Codex wrapper runs without a shell, so `ls *.md` looked for a file literally named `*.md`. Commands with globs or comments are no longer wrapped.
- Reject `rg --hostname-bin`, which can run a program, from the read-only allowlist.
- Add `scripts/e2e_codex.py`, which tests a hook on the real Codex binary with a scripted local model and no account.
- Skip the CLI's imports for hook events.

### Retrieval

- Add `sbs find` and `sbs_find`: ranked exact passages across recent receipts. The index exists in memory for one query, with FTS5 or a Python ranking.
- Add JavaScript and TypeScript symbols through a dependency-free tokenizer.
- Add `sbs def`, `sbs refs`, `sbs symbol` and `sbs map --ranked`, with the matching MCP tools. References are name matches and are labeled as such.
- `sbs read` now keeps trailing whitespace and CRLF, so its output really is exact.

### Measurement

- **Fix:** `sbs usage` skipped logs over 40 MB and stopped after 1,500 files without saying so, and counted a response again in each log repeating it. Totals on existing machines will change. It now reads every log, counts each response once, also reads `archived_sessions` and `CLAUDE_CONFIG_DIR`, reports unreadable files and accepts `--since`.
- `sbs stats` now reports hook coverage, hook time, restores and disk use, and offers `--json`. These counters hold sizes only.
- Add `sbs doctor` hook wiring and FTS5 status.
- Add command fixtures to the fidelity benchmark, plus `scripts/benchmark_runtime.py`, `scripts/benchmark_retrieval.py`, `scripts/replay_transcripts.py` and `scripts/paired_runner.py`.
- Publish paired trials on real Claude Code in [docs/TOKEN_EFFICIENCY.md](docs/TOKEN_EFFICIENCY.md).

### Resources and protocol

- `sbs compress` holds at most 16 MiB in memory, keeps non-UTF-8 input byte for byte, and keeps its input when storage fails.
- `sbs purge` returns the space to the disk. Add the opt-in `SBS_RECEIPT_MAX_MB`; nothing is evicted without it.
- `--max-chars` now defaults to `SBS_MAX_CHARS`, which the CLI used to ignore.
- MCP: use the JSON-RPC codes for invalid parameters, internal errors and parse errors, answer parse errors, counter-offer the newest supported protocol version, and annotate read-only tools.
- Protect receipts from recognizable credentials, including token formats, URLs with a password and values split by terminal colors. Add the opt-in `SBS_RECEIPT_RETENTION_DAYS`, refuse a symlinked store, and bound MCP input frames while reading.
- Add a paired-session analyzer.
- CI also runs Python 3.14 and macOS.

### Earlier unreleased

- Add a macOS installer/repair script, without requiring pipx, Git or a source checkout.
- Persist the CLI path with a backup, refresh only existing SBS integrations and run the built-in reference-token benchmark.
- Clarify copyable installation examples, optional dependencies, receipt IDs and common terminal errors.

## 0.2.0 — 2026-10-27

- Retrieve exact receipt line ranges or literal matches with context from CLI and MCP.
- Preserve recognized JSON failure records, including middle-array anomalies and exact diagnostic values.
- Keep detected source code, diffs and CSV unchanged in auto mode; keep Python tracebacks intact in auto/logs/plain modes.
- Keep recognized diagnostic lines whole or return the original when the requested view cannot contain them.
- Add optional named reference-token counts without making tiktoken a core dependency or claiming provider-billed savings.
- Add an offline, multi-fixture benchmark with baseline comparison, fidelity checks and explicit measurement boundaries.
- Document a practical measurement roadmap.

- Preserve original command output and exit status when local reduction/storage fails.
- Preserve third-party hooks sharing a group with SBS during reinstall/removal.
- Keep failed Claude tool output unchanged when an exit status is provided.
- Bind zsh launchers to the installed Python interpreter, including virtual environments.
- Make literal-search case matching consistent with and without ripgrep.
- Add regression tests, a command guide, troubleshooting and clearer integration limits.

## 0.1.0 — 2026-10-26

- New offline Python CLI with 7 local MCP tools.
- Adaptive previews for JSON, logs, CSV, diffs, code and text.
- Private SQLite restoration receipts and per-project notes.
- Python AST symbol map, targeted search and exact line reading.
- Opt-in Claude Code and Codex hook integrations.
- Reversible zsh launcher and colored terminal banner.
- Logged token usage counters separated from character reduction metrics.
- Deterministic benchmarks and offline unit tests.
