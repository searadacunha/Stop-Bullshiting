# Agent integrations

Hook adapters are opt-in and client-version dependent. Unit tests verify the JSON
fixtures used by this project; they are not an end-to-end compatibility matrix.
`sbs doctor` checks executable/configuration presence, not whether a client accepts
output replacement. The explicit `sbs run -- <command>` workflow is independent
of hook support.

## Verified against installed clients

Run on **2026-11-01** with the real binaries, not simulated events.

| Check | Claude Code 2.1.316 | Codex 0.166.0 |
|:--|:--|:--|
| Route used by SBS | `PostToolUse` with `updatedToolOutput` | `PreToolUse` with `permissionDecision: allow` and `updatedInput.command` |
| The model receives the replacement | Yes, for Bash, Grep, Read and Glob | Yes |
| A failing command | `PostToolUseFailure` fires instead, so SBS never touches it | Runs through the wrapper, which passes failures through unchanged |
| Exit status stays visible | Yes | Yes, in Codex's own output envelope |
| A permission rule still applies | SBS emits no permission decision | Only because SBS skips commands named by a rule: see below |

Claude Code was run with `claude -p` and a cheap model. Codex was run through `scripts/e2e_codex.py`: a throwaway `CODEX_HOME`, a scripted local model, no account, and the hook approved the normal way, so the script records exactly what a model would receive. Other client versions can differ. `sbs doctor` shows which hooks are wired and `sbs stats` shows what they changed.

Documented contracts: [Claude Code hooks](https://code.claude.com/docs/en/hooks#posttooluse-decision-control), [Codex hooks](https://developers.openai.com/codex/hooks).

## Claude Code

Run `sbs setup claude`. The installer merges one `PostToolUse` command hook with the matcher `Bash|Grep` into `~/.claude/settings.json`. It does not alter `permissions`, credentials or model settings. Replacing a result grants nothing: the tool has already run, and stderr is preserved.

| Tool | What the hook does |
|:--|:--|
| Bash | Applies a command-aware view when the program is recognized, including after `cd` and through `head` or `tail`. Allowlisted read-only commands also lose repeated lines and JSON spacing. Any command loses terminal colors and overwritten progress. Everything else stays unchanged. |
| Grep | Groups `content` results by file, printing each path once. |
| Read | Never changed. Claude Code numbers replaced content from line 1, and in a real session a model reported line 3 for a line that was really line 501. |
| Glob | Never changed: its result is a list of paths, and each one is a fact. |

`SBS_COMPRESS_ALL=1` adds head-and-tail previews for every command. It is **not recommended**: in paired trials models did not fetch omitted lines back and answered wrongly instead. Commands run through a wrapper such as `rtk` are recognized, but SBS can only restore what it received.

## Codex

Run `sbs setup codex`. The installer merges one `PreToolUse` / `Bash` command hook into `~/.codex/hooks.json`. The hook accepts only recognized read-only shell commands without redirects, pipelines, substitutions or shell operators, and rewrites them to `python -m stop_bullshiting shell '<original command>'`.

Then open **`/hooks` in Codex** to review and trust the exact new or changed hook. Unreviewed non-managed hooks are skipped, so writing `hooks.json` does not activate it. SBS does not bypass that trust review.

Measured behavior of this route:

- **Exec-policy rules:** Codex checks them against the rewritten command, so a `forbidden` rule on `git log` did not fire once the command was wrapped. SBS now leaves a command alone when any rule file names its program, or when a rule file cannot be read.
- **Sandbox:** `allow` did not widen it. A rewritten write in a read-only sandbox was refused.
- **Reduction depends on the sandbox.** With no temporary directory (read-only), the wrapper runs the command directly. With a store outside the writable roots (workspace-write, default state directory), output stays whole and stderr carries one `Reduction unavailable` line. Reduction happens only with full access or a writable `SBS_HOME`.
- **Tool coverage:** the hook sees `exec_command` calls. Sessions driving tools through Codex's JavaScript code mode did not reach it: 0 such calls in the 120 most recent sessions on the test machine.

`PostToolUse` with `decision: "block"` can also replace a Codex result without a rewrite. SBS does not use it: `tool_response` is plain text with no exit status, the replacement removes the `Process exited with code N` envelope, and Codex records the call as an error.

If your Codex version does not support this hook schema, use `sbs run` explicitly instead, or the MCP server.

## Shell logo

`sbs setup shell` installs two zsh functions delimited by comments. Each uses the
absolute Python interpreter that performed setup, so it also works with a virtual
environment that is not active in the new terminal. Keep that interpreter and
installation available; rerun setup after moving or reinstalling them. The
launcher invokes the *real* `claude` or `codex` executable after showing an ANSI
banner. The banner itself does not reduce output. Existing functions within the
`STOP-BULLSHITING` marker are replaced only when reinstalling; other `.zshrc`
lines are left in place. Open a new terminal for changes to take effect.

## MCP server

`sbs mcp` runs stdio JSON-RPC with no network listener. Tools are explicit and bounded. The server does not mutate an agent configuration or automatically intercept other MCP responses.

The 12 tools provide text compaction/restore, ranked receipt search, source navigation, and local project notes. Read-only tools carry the `readOnlyHint` annotation. `sbs_read` and `sbs_symbol_source` always yield exact line contents. `sbs_references` matches names without resolving scope and says so in its answer.

`sbs_restore` accepts a receipt ID and optional `start`, `end`, `query`, `context` (default 2) and `limit` (default 50) arguments. Ranges are 1-based and inclusive; queries are case-sensitive literals. Selected lines retain their exact contents with line numbers added. Responses include counts and explicit omission notices, and never cut an oversized line silently.

An unqualified restore of an original at most 20,000 characters remains unchanged. Larger originals or explicit selections use bounded views, including metadata, with at most 50 source lines considered by default. Use a later range or a literal query to reach diagnostics beyond the initial response. For an uncapped complete original, use `sbs show <receipt-id>` locally.

For example, the arguments below retrieve matching lines with their neighbors:

```json
{"receipt":"a1b2c3d4e5f601","query":"request-0055","context":3,"limit":20}
```

## Removal

```bash
sbs setup claude --remove
sbs setup codex --remove
sbs setup shell --remove
```

Backups are written beside the original files with a UTC timestamp. No user credentials are accessed or changed.
Removing or reinstalling SBS preserves unrelated hooks even when they share its
event group and matcher.
