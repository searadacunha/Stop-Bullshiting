"""Noninvasive integrations for Claude Code and Codex hooks."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

from .compress import compress

HOOK_MATCHER = "Bash"
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
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts or any("\n" in part or "\r" in part for part in parts):
        return False
    first = Path(parts[0]).name
    if first == "git" and len(parts) >= 2:
        return parts[1] in {"status", "log", "diff", "show", "ls-files"} and not any(
            arg.startswith("--output") or arg in ("-o", "--ext-diff") for arg in parts[2:])
    if first in {"rg", "grep"}:
        return not any(arg in ("--pre", "--pre-glob", "-P", "--passthru")
                       or arg.startswith("--pre=") or arg.startswith("--pre-glob=") for arg in parts[1:])
    if first == "ls":
        return True
    return False


def _decoded_event(raw: str) -> dict:
    try:
        event = json.loads(raw)
        return event if isinstance(event, dict) else {}
    except (ValueError, TypeError):
        return {}


def claude_post_hook(raw: str) -> str:
    event = _decoded_event(raw)
    if event.get("tool_name") != "Bash" or event.get("hook_event_name") != "PostToolUse":
        return ""
    tool_input = event.get("tool_input") or {}
    response = event.get("tool_response") or {}
    if not isinstance(tool_input, dict) or not isinstance(response, dict):
        return ""
    command = tool_input.get("command", "")
    if not safe_read_command(command) and os.environ.get("SBS_COMPRESS_ALL") != "1":
        return ""
    stdout = response.get("stdout")
    if not isinstance(stdout, str) or "\x00" in stdout or response.get("interrupted") or response.get("isImage"):
        return ""
    if any(response[key] != 0 for key in ("exitCode", "exit_code", "returncode") if key in response):
        return ""
    if not isinstance(response.get("stderr", ""), str):
        return ""
    try:
        result = compress(stdout, source="claude:Bash")
    except Exception:
        return ""  # hooks must fail open, never interfere with the agent
    if not result.receipt:
        return ""
    replacement = dict(response)
    replacement["stdout"] = result.text
    # Claude requires a tool-appropriate shape. Preserve all original flags.
    replacement.setdefault("stderr", "")
    replacement.setdefault("interrupted", False)
    replacement.setdefault("isImage", False)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                    "updatedToolOutput": replacement}}, ensure_ascii=False)


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


def _hook_definition(agent: str) -> dict:
    return {"matcher": HOOK_MATCHER, "hooks": [
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
