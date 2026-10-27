# Changelog

## Unreleased

- Add a macOS installer/repair script for the reviewed 0.2.0 release, without requiring pipx, Git or a source checkout.
- Persist the CLI path with a backup, refresh only existing SBS integrations and run the built-in reference-token benchmark.
- Clarify copyable installation examples, optional dependencies, receipt IDs and common terminal errors.

## 0.2.0 — 2026-10-27

- Retrieve exact receipt line ranges or literal matches with context from CLI and MCP.
- Preserve recognized JSON failure records, including middle-array anomalies and exact diagnostic values.
- Keep detected source code, diffs and CSV unchanged in auto mode; keep Python tracebacks intact in auto/logs/plain modes.
- Keep recognized diagnostic lines whole or return the original when the requested view cannot contain them.
- Add optional named reference-token counts without making tiktoken a core dependency or claiming provider-billed savings.
- Add an offline, multi-fixture benchmark with baseline comparison, fidelity checks and explicit measurement boundaries.
- Document the comparison with RTK, Headroom, Aider, Repomix and LLMLingua, plus a practical measurement roadmap.

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
