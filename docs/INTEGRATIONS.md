# Agent integrations

## Claude Code

Run `sbs setup claude`. The installer merges one `PostToolUse` / `Bash` command hook into `~/.claude/settings.json`. It does not alter `permissions`, credentials or model settings. Claude Code versions supporting `updatedToolOutput` can replace model-visible `stdout` for eligible large, successfully completed Bash results. Stderr is preserved and the source tool has already run.

The hook defaults to a narrow read-only command allowlist: `git status/log/diff/show/ls-files`, `rg`, `grep`, and `ls`. Arbitrary commands can be explicitly allowed with `SBS_COMPRESS_ALL=1`, but this is **not recommended**: model-critical data can be truncated. Disabling the hook is safer than attempting to compress binary or security-sensitive output.

If the hook response is not compatible with your installed Claude Code version, the application continues normally. Test first with `sbs doctor` and a disposable project.

## Codex

Run `sbs setup codex`. The installer merges one `PreToolUse` / `Bash` command hook into `~/.codex/hooks.json`. The hook accepts only recognized read-only shell commands without redirects, pipelines, substitutions or shell operators, and rewrites them to `python -m stop_bullshiting shell '<original command>'`.

The wrapper parses the arguments with `shlex`, executes via `subprocess` (no nested shell), retains the original exit code, leaves failures unchanged and stores complete original output locally if it is reduced. For unsupported commands it emits no hook action.

Codex `PreToolUse` input rewrites involve `permissionDecision: allow`, so the allowlist must not be expanded to commands that modify data or need interactive approval. If your Codex version does not support this hook schema, use `sbs run` explicitly instead.

## Shell logo

`sbs setup shell` installs two zsh functions delimited by comments. It invokes the *real* `claude` or `codex` executable after showing an ANSI startup banner. Existing aliases/functions within the `STOP-BULLSHITING` marker are replaced only when reinstalling; other `.zshrc` lines are left in place. Open a new terminal for changes to take effect.

## MCP server

`sbs mcp` runs stdio JSON-RPC with no network listener. Tools are explicit and bounded. The server does not mutate an agent configuration or automatically intercept other MCP responses.

The 7 tools provide text compaction/restore, source navigation, and local project notes. `sbs_read` always yields exact line contents. `sbs_restore` returns at most 20,000 characters over MCP; for longer originals use the CLI.

## Removal

```bash
sbs setup claude --remove
sbs setup codex --remove
sbs setup shell --remove
```

Backups are written beside the original files with a UTC timestamp. No user credentials are accessed or changed.
