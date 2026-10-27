# Changelog

## Unreleased

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
