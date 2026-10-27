# Agent integrations

Hook adapters are opt-in and client-version dependent. Unit tests verify the JSON
fixtures used by this project; they are not an end-to-end compatibility matrix.
`sbs doctor` checks executable/configuration presence, not whether a client accepts
output replacement. The explicit `sbs run -- <command>` workflow is independent
of hook support.

## Documented contracts

Checked against upstream documentation on **2026-10-27**:

| Client | Contract used by SBS | Primary reference |
|:--|:--|:--|
| Claude Code | `PostToolUse.updatedToolOutput` with the structured Bash response shape | [Claude Code hooks reference](https://code.claude.com/docs/en/hooks#posttooluse-decision-control) |
| Codex | `PreToolUse`, canonical hook name `Bash`, `permissionDecision: allow` with `updatedInput.command` | [Codex hooks](https://developers.openai.com/codex/hooks) |

These are documented interfaces, not a claim that the user's installed client
version has been tested. Older versions can differ.

## Claude Code

Run `sbs setup claude`. The installer merges one `PostToolUse` / `Bash` command hook into `~/.claude/settings.json`. It does not alter `permissions`, credentials or model settings. Claude Code versions supporting `updatedToolOutput` can replace model-visible `stdout` for eligible large, successfully completed Bash results. Stderr is preserved and the source tool has already run.

The hook defaults to a narrow read-only command allowlist: `git status/log/diff/show/ls-files`, `rg`, `grep`, and `ls`. Arbitrary commands can be explicitly allowed with `SBS_COMPRESS_ALL=1`, but this is **not recommended**: model-critical data can be truncated. Disabling the hook is safer than attempting to compress binary or security-sensitive output.

If the hook response is not compatible with your installed Claude Code version, the application continues normally. Test first with `sbs doctor` and a disposable project.

## Codex

Run `sbs setup codex`. The installer merges one `PreToolUse` / `Bash` command hook into `~/.codex/hooks.json`. The hook accepts only recognized read-only shell commands without redirects, pipelines, substitutions or shell operators, and rewrites them to `python -m stop_bullshiting shell '<original command>'`.

Then open **`/hooks` in Codex** to review and trust the exact new or changed hook.
Current Codex documentation states that unreviewed non-managed hooks are skipped;
writing `hooks.json` does not by itself activate the hook. SBS does not bypass
that trust review.

The wrapper parses the arguments with `shlex`, executes via `subprocess` (no nested shell), retains the original exit code, leaves failures unchanged and stores complete original output locally if it is reduced. For unsupported commands it emits no hook action.

Codex `PreToolUse` input rewrites involve `permissionDecision: allow`, so the allowlist must not be expanded to commands that modify data or need interactive approval. If your Codex version does not support this hook schema, use `sbs run` explicitly instead.

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

The 7 tools provide text compaction/restore, source navigation, and local project notes. `sbs_read` always yields exact line contents.

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
