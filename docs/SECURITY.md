# Security model

## Threats and mitigations

- **Loss of critical tool output**: full originals remain available via a local receipt unless storage was explicitly disabled. The model-visible view is lossy; verify any critical fact against the original.
- **Secrets inside tool output**: SQLite receipts use zlib only (not encryption). This tool does not sanitize secrets; use `--no-store` for sensitive output and encrypt your drive.
- **Unexpected command execution**: the automatic Codex wrapper recognizes only basic read-only commands, rejects shell metacharacters and calls `subprocess.run` with an argument list (no shell execution). Explicit `sbs run` executes only the command supplied by the user or agent.
- **Configuration corruption**: install routines merge JSON and backup any preexisting contents before changing them. Invalid JSON is not overwritten. The zsh integration has removable markers.
- **Dependency compromise**: no mandatory runtime third-party Python dependencies, no updater and no external service. The optional `tokens` extra installs tiktoken; explicitly requesting a reference count may download a public vocabulary on first use. Tool output is not uploaded.
- **Agent hallucination / suppressed failures**: failing wrapped commands pass through their full output and original process return code. Search/excerpt routines return exact line text and file positions.

## User data

Default state directory: `~/.local/state/stop-bullshiting/`. Override with `SBS_HOME`. Reversible receipts are never uploaded by the package. Notes are not encrypted. Do not commit local state files, API keys, token dumps or session logs to a public repository.

## Reporting vulnerabilities

Open a GitHub issue with a minimal reproduction that does not expose credentials. Do not include sensitive logs or private files.
