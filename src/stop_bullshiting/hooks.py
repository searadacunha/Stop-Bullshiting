"""Noninvasive integrations for Claude Code and Codex hooks."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import min_chars
from .store import count_event

HOOK_MATCHER = "Bash"
# Not Read: Claude Code numbers the replacement from 1, so a preview would give a
# model wrong line numbers for the file. A real session reported line 3 for line 501.
CLAUDE_MATCHER = "Bash|Grep"
SBS_MARKER = "stop_bullshiting hook"
SHELL_START = "# >>> STOP-BULLSHITING >>>"
SHELL_END = "# <<< STOP-BULLSHITING <<<"


def _hook_command(agent: str) -> str:
    return f"{shlex.quote(sys.executable)} -m stop_bullshiting hook {agent}"


def safe_read_command(command: str) -> bool:
    """Conservative allowlist: only simple read-only commands, no shell operators.

    For Codex, PreToolUse hook rewriting requires 'allow', so never auto-approve
    anything with shell expansion, pipeline, redirection or ambiguous commands.
    """
    if not isinstance(command, str) or len(command) > 2000 or "\n" in command:
        return False
    if re.search(r"[\x00\r;|&><`$(){}\\]", command):
        return False
    # The wrapper runs without a shell, so globs and comments would change meaning.
    if re.search(r"[*?\[\]!#~]|(?:^|\s)=", command):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts or any("\n" in part or "\r" in part for part in parts):
        return False
    # A Codex hook's 'allow' also grants permission. Do not auto-approve
    # filesystem reads outside the local tree, even for read-only programs.
    if any(part.startswith(("/", "~")) or ".." in Path(part).parts for part in parts[1:]):
        return False
    # Do not auto-approve arbitrary executables just because they are named ls or git.
    # Only bare command names can enter this conservative allowlist.
    first = parts[0]
    if first == "git" and len(parts) >= 2:
        return parts[1] in {"status", "log", "diff", "show", "ls-files"} and not any(
            arg.startswith(("--output", "-o")) or arg in ("--ext-diff", "--textconv") for arg in parts[2:])
    if first in {"rg", "grep"}:
        return not any(arg in ("--pre", "--pre-glob", "-P", "--passthru")
                       or arg.startswith(("--pre=", "--pre-glob=", "--hostname-bin")) for arg in parts[1:])
    if first == "ls":
        return True
    return False


def _decoded_event(raw: str) -> dict:
    try:
        event = json.loads(raw)
        return event if isinstance(event, dict) else {}
    except (ValueError, TypeError):
        return {}


def _claude_payload(tool: str, tool_input: dict, response: dict):
    """Return (text, rebuild, command) for a replaceable result, else None.

    Shapes were captured from real tool responses; the replacement must match them.
    """
    if tool == "Bash":
        stdout = response.get("stdout")
        if (not isinstance(stdout, str) or "\x00" in stdout or response.get("interrupted")
                or response.get("isImage") or not isinstance(response.get("stderr", ""), str)):
            return None
        if any(response[key] != 0 for key in ("exitCode", "exit_code", "returncode") if key in response):
            return None
        command = tool_input.get("command", "")

        def rebuild(text: str) -> dict:
            # Claude requires a tool-appropriate shape. Preserve all original flags.
            replacement = dict(response)
            replacement["stdout"] = text
            replacement.setdefault("stderr", "")
            replacement.setdefault("interrupted", False)
            replacement.setdefault("isImage", False)
            return replacement

        return stdout, rebuild, command
    if tool == "Grep":
        content = response.get("content")
        if response.get("mode") != "content" or not isinstance(content, str):
            return None
        return content, lambda text: {**response, "content": text}, "grep"
    return None


def claude_post_hook(raw: str) -> str:
    event = _decoded_event(raw)
    tool = event.get("tool_name")
    if event.get("hook_event_name") != "PostToolUse" or tool not in ("Bash", "Grep"):
        return ""
    tool_input = event.get("tool_input") or {}
    response = event.get("tool_response") or {}
    if not isinstance(tool_input, dict) or not isinstance(response, dict):
        return ""
    source = f"claude:{tool}"
    started = time.perf_counter()
    try:
        payload = _claude_payload(tool, tool_input, response)
        if payload is None:
            count_event(source, "not_eligible", micros=int((time.perf_counter() - started) * 1e6))
            return ""
        text, rebuild, command = payload
        if len(text) <= min_chars():
            count_event(source, "unchanged", len(text), len(text), int((time.perf_counter() - started) * 1e6))
            return ""
        from .compress import compress  # only large outputs pay for this import
        # Trials showed agents rarely fetch omitted lines back, so no head/tail previews here:
        # recognized commands get their view, allowlisted ones lose only repeats and spacing.
        everything = os.environ.get("SBS_COMPRESS_ALL") == "1"
        result = compress(text, source=source, command=command, generic=everything,
                          cleanup=everything or (tool == "Bash" and safe_read_command(command)))
    except Exception:
        return ""  # hooks must fail open, never interfere with the agent
    micros = int((time.perf_counter() - started) * 1e6)
    count_event(source, "reduced" if result.receipt else "unchanged", len(text), len(result.text), micros)
    if not result.receipt:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                    "updatedToolOutput": rebuild(result.text)}}, ensure_ascii=False)


def _codex_rule_applies(program: str, cwd) -> bool:
    """True when an exec-policy rule may mention this program, or when that cannot be ruled out.

    Codex checks its rules against the rewritten command, so wrapping a command would
    hide it from a rule written for the original. Such a command is left to Codex.
    """
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    folders = [home / "rules"] + ([Path(cwd) / ".codex" / "rules"] if isinstance(cwd, str) and cwd else [])
    try:
        for folder in folders:
            for file in folder.glob("*.rules"):
                text = file.read_text(encoding="utf-8", errors="replace")
                if f'"{program}"' in text or f"'{program}'" in text:
                    return True
    except OSError:
        return True
    return False


def codex_pre_hook(raw: str) -> str:
    event = _decoded_event(raw)
    if event.get("tool_name") != "Bash" or event.get("hook_event_name") != "PreToolUse":
        return ""
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command", "")
    if not safe_read_command(command) or command.strip().startswith("sbs "):
        return ""
    if _codex_rule_applies(shlex.split(command)[0], event.get("cwd")):
        return ""
    # Quote original command as one argument, so no extra shell operators appear.
    # Preserve the original tool_input fields (e.g. timeout and workdir).
    rewrite = dict(tool_input)
    rewrite["command"] = (f"{shlex.quote(sys.executable)} -m stop_bullshiting shell "
                          f"{shlex.quote(command)}")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                    "permissionDecision": "allow",
                                                    "updatedInput": rewrite}})


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return loaded


def _save_with_backup(path: Path, content: str) -> str:
    """Write through a temporary file, backup existing content, preserve permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        previous = path.read_text(encoding="utf-8")
        if previous == content:
            return f"unchanged: {path}"
        when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(path.name + f".backup-{when}")
        backup.write_text(previous, encoding="utf-8")
        if os.name == "posix":
            os.chmod(backup, 0o600)
    tmp = path.with_name(path.name + ".sbs-tmp")
    tmp.write_text(content, encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    return f"updated: {path} (existing file backed up)"


def run_hook(agent: str, raw: str) -> str:
    """Answer one hook event. Any failure means no answer: a hook error must not reach the tool."""
    try:
        return claude_post_hook(raw) if agent == "claude" else codex_pre_hook(raw)
    except Exception:
        return ""


def hook_main(agent: str) -> int:
    """Process entry point for one hook event. Exit 2 would block a tool or replace its result."""
    try:
        reply = run_hook(agent, sys.stdin.buffer.read().decode("utf-8", "replace"))
        if reply:
            sys.stdout.write(reply + "\n")
    except Exception:
        pass
    return 0


def _hook_definition(agent: str) -> dict:
    return {"matcher": CLAUDE_MATCHER if agent == "claude" else HOOK_MATCHER, "hooks": [
        {"type": "command", "command": _hook_command(agent), "timeout": 10}
    ]}


def _without_our_hooks(group):
    """Remove individual SBS entries, retaining neighboring hooks and metadata."""
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return group
    existing = group["hooks"]
    kept = [hook for hook in existing if not (
        isinstance(hook, dict) and SBS_MARKER in str(hook.get("command", "")))]
    if len(kept) == len(existing):
        return group
    return {**group, "hooks": kept} if kept else None


def setup_agent(agent: str, *, remove: bool = False, root: Path | None = None) -> str:
    if agent not in ("claude", "codex"):
        raise ValueError("Unsupported agent")
    root = root or Path.home()
    path = (root / ".claude" / "settings.json") if agent == "claude" else (root / ".codex" / "hooks.json")
    doc = _read_json(path)
    hooks_root = doc.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        raise ValueError(f"Invalid hooks structure in {path}")
    event = "PostToolUse" if agent == "claude" else "PreToolUse"
    groups = hooks_root.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"Invalid {event} configuration in {path}")
    # Remove only this project's hook, keeping every other hook unmodified.
    groups[:] = [cleaned for group in groups if (cleaned := _without_our_hooks(group)) is not None]
    if not remove:
        groups.append(_hook_definition(agent))
    if not groups:
        del hooks_root[event]
    if not hooks_root:
        del doc["hooks"]
    if remove and not path.exists():
        return f"nothing to remove: {path}"
    return _save_with_backup(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def setup_shell(*, remove: bool = False, root: Path | None = None) -> str:
    root = root or Path.home()
    target = root / ".zshrc"
    previous = target.read_text(encoding="utf-8") if target.exists() else ""
    cleaned = re.sub(re.escape(SHELL_START) + r".*?" + re.escape(SHELL_END) + r"\n?",
                     "", previous, flags=re.DOTALL)
    if remove:
        if not target.exists():
            return "shell aliases not installed"
        return _save_with_backup(target, cleaned)
    snippet = (f"{SHELL_START}\n"
               "# Display SBS banner before launching actual CLI binaries.\n"
               f"claude() {{ command {shlex.quote(sys.executable)} -m stop_bullshiting launch claude -- \"$@\"; }}\n"
               f"codex() {{ command {shlex.quote(sys.executable)} -m stop_bullshiting launch codex -- \"$@\"; }}\n"
               f"{SHELL_END}\n")
    return _save_with_backup(target, cleaned.rstrip("\n") + "\n\n" + snippet)
