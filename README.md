<div align="center">

# STOP-BULLSHITING

**Less noise. More signal. Your machine. Your control.**

*An open-source, local-first context reduction toolkit for coding agents.*

```text
  ███████╗████████╗ ██████╗ ██████╗
  ██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗
  ███████╗   ██║   ██║   ██║██████╔╝
  ╚════██║   ██║   ██║   ██║██╔═══╝
  ███████║   ██║   ╚██████╔╝██║
  ╚══════╝   ╚═╝    ╚═════╝ ╚═╝

  B U L L S H I T I N G
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LESS NOISE   •   MORE SIGNAL   •   LOCAL FIRST
```

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Offline first](https://img.shields.io/badge/network-not_required-149e85)

</div>

## Why it exists

Coding agents read huge logs, repetitive terminal output, massive JSON objects, code they never needed, and the same project notes again and again. That can waste context window space. **Stop-Bullshiting** replaces long outputs with bounded, inspectable previews, keeps originals available on disk, and makes focused retrieval easy.

This project does **not** increase subscription usage limits or create extra tokens. A smaller output does not guarantee a cheaper session or an equally accurate result. It does not promise a fixed percentage of savings. You can benchmark characters locally and inspect token counters recorded by agents.

## Features

| Feature | What it does |
|:--|:--|
| **Output reduction** | Detects JSON, logs, diffs, code, CSV and generic text; keeps important errors and structural context |
| **Reversible receipts** | Saves full original locally, recoverable using `sbs show <id>` |
| **Claude Code hook** | Optional `PostToolUse` reduction of supported Bash stdout; unrelated tools untouched |
| **Codex hook** | Optional `PreToolUse` wrapping of a narrow allowlist of read-only terminal commands |
| **Local MCP server** | Exposes search, AST symbols, exact lines, reversible compression and notes over stdio |
| **Symbol navigation** | Precise Python AST class/function locations, targeted excerpts |
| **Persistent notes** | Per-project local SQLite notes, never sent to an external indexing service |
| **Real usage counters** | Reads available local Claude/Codex session usage records without inventing tokens |
| **Benchmark mode** | Reports character reduction of synthetic examples or your real output samples |
| **Terminal identity** | ANSI banner when launching Claude Code or Codex |

## Install

Python 3.10+; no runtime Python dependencies. On macOS or Linux:

```bash
# Clone after the repository has been published, or use an extracted release archive.
cd Stop-Bullshiting
python3 -m pip install --user .

sbs banner
sbs doctor
sbs bench
```

If your Python is externally managed (common with Homebrew), use [pipx](https://pipx.pypa.io/):

```bash
pipx install .
```

A virtual environment also works. The `sbs` and `stop-bullshiting` commands are identical.

## Publish the source on GitHub

The repository contains a one-command publishing script for the authenticated GitHub CLI:

```bash
# Run from an extracted Stop-Bullshiting release folder.
gh auth login                 # once, if not already authenticated
bash scripts/publish_github.sh
```

The script creates `searadacunha/Stop-Bullshiting` as a **public** repository, commits only this project's source and documentation, and pushes the initial `main` branch. It never force-pushes an existing remote. You must have `gh` installed and authenticated. Set `GITHUB_OWNER` if publishing under another account.

## Connect to Claude Code and Codex

```bash
# Optional hook configuration: local, reversible, with backups.
sbs setup claude
sbs setup codex

# Optional zsh launch functions: makes "claude" and "codex" show the logo.
sbs setup shell

# Or configure all three at once (explicit opt-in):
sbs setup all
```

Open a **new terminal** after `sbs setup shell`. Your original CLI binaries still run. The hook integrations must already be supported by your installed versions of Claude Code and Codex. No authentication configuration is touched.

To uninstall all integrations without uninstalling the package:

```bash
sbs setup all --remove
```

Existing hook configurations are merged, not overwritten; every changed file is backed up. `~/.zshrc` is used only if you explicitly enable the shell launcher.

**Important:** Claude Code can rewrite a tool result after execution. Codex currently takes a different route: only narrowly allowlisted *read-only* commands are wrapped before execution. These are not interchangeable features. Both integrations deliberately fail open when an output cannot be handled safely.

## Real workflows

### Reduce terminal or pipeline output

```bash
# Original exit status is preserved; stderr and failing-command output are not compacted.
sbs run -- git log --oneline -1000
sbs run -- rg "TODO" .
sbs run -- python3 -m unittest discover -s tests

# Or explicitly pipe any text:
git log --oneline -1000 | sbs compress --mode plain --stats
cat run.log | sbs compress --mode logs --max-chars 2400
```

If a result is reduced, it includes an ID:

```text
... [193 lines omitted] ...
[SBS logs: 18602 → 2110 chars; full original: sbs show a1b2c3d4e5f601]
```

Recover everything without loss:

```bash
sbs show a1b2c3d4e5f601
```

You can disable local storage using `sbs compress --no-store` or `sbs run --no-store -- command`.

### Search code without dumping whole files

```bash
sbs map . --name validate_transaction
sbs search "create_signature" .
sbs read src/my_module.py 88 135
```

Python symbols use the actual syntax tree and report accurate start/end line numbers. Search uses `rg` if available, with a safe Python fallback. Searches are bounded; they do not automatically read entire repositories.

### Keep compact project notes

```bash
sbs note put "test-command" "python3 -m unittest discover -s tests"
sbs note search "test"
sbs note get "test-command"
sbs note delete "test-command"
```

Notes are scoped to the current working directory unless `--scope` is specified. Avoid storing secrets.

### Analyze consumption

```bash
sbs stats             # Observed reduction in characters in SBS receipts
sbs usage claude      # Real counters if locally recorded by Claude Code
sbs usage codex       # Real counters if locally recorded by Codex
sbs usage all --json
sbs bench --file sample.log --mode logs
```

**Different metrics, different meaning:** `sbs stats` and `sbs bench` measure characters, *not* actual model token cost. `sbs usage` reads token usage records, but cannot assign causality to this tool. A reliable savings claim needs an A/B test with equivalent tasks, model, caching and context.

### MCP: 7 local tools

```bash
sbs mcp
```

MCP uses stdio JSON-RPC; no server port and no cloud embeddings. Register the command as an MCP server in a supported agent. For example, if the installed client supports the following `mcp add` interface:

```bash
claude mcp add stop-bullshiting -- sbs mcp
codex mcp add stop-bullshiting -- sbs mcp
```

If a client does not support that command format, use its MCP configuration UI and set command=`sbs`, args=`["mcp"]`. Registration differs by version and is not performed automatically by `setup`.

MCP tools: `sbs_compact`, `sbs_restore`, `sbs_symbols`, `sbs_search`, `sbs_read`, `sbs_note_save`, `sbs_note_find`.

## Architecture

```text
 Claude Code ──[optional PostToolUse]───┐
                                       │
 Codex ──[optional read-only wrapper]──┼──> SBS Core
                                       │      │
 Terminal / Scripts ──[sbs run]────────┤      ├── JSON / logs / diff / text views
                                       │      ├── exact source range / AST map
 MCP clients ──[stdio JSON-RPC]────────┘      ├── local project memory
                                              └── private SQLite originals + metrics
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) and [docs/SECURITY.md](docs/SECURITY.md).

## Safety, privacy and limitations

- No external HTTP requests, account credentials, proxy, model inference or subscription-limit changes.
- Original outputs and notes are stored locally in SQLite, **compressed but not encrypted**. They can contain secrets if your commands emit them. Use `--no-store` for sensitive data and clean stored receipts with `sbs purge --older-than-days 30`.
- Truncation is **not semantically lossless**. The receipt guarantees access to the original, not identical model decisions. Errors are prioritized but can still be omitted under very small budgets.
- Invalid/corrupt configs are rejected rather than overwritten. Existing files are backed up; each installer can be reversed.
- `sbs run` captures stdout for bounded postprocessing. Use `sbs run --raw -- <command>` for interactive commands or anything needing streaming stdout.
- Automatic hooks cover only clearly recognized command patterns. They are not a generalized proxy for every tool call or MCP message.
- The default local benchmark is synthetic and does not establish response-quality equivalence.

## Development

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
python3 -m stop_bullshiting doctor
```

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
