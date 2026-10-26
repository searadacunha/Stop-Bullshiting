import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.hooks import (claude_post_hook, codex_pre_hook, safe_read_command,
                                    setup_agent, setup_shell)
from stop_bullshiting.store import get_receipt


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": str(Path(self.temp.name)/"state"),
                                        "SBS_MIN_GAIN": "0.01"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_allowlist(self):
        for cmd in ("git status --short", "git diff --stat", "rg 'function name' src", "ls -la"):
            self.assertTrue(safe_read_command(cmd), cmd)
        for cmd in ("rm -rf .", "git push", "pytest -q", "git diff; rm -rf .",
                    "rg file | sh", "git status $(touch /tmp/evil)", "ls > file",
                    "git -c alias.x=status x", "rg --pre=curl foo"):
            self.assertFalse(safe_read_command(cmd), cmd)

    def test_claude_replacement_only_safe_big_stdout(self):
        data = "\n".join("2026 INFO status successful" for _ in range(240))
        event = {"tool_name": "Bash", "hook_event_name": "PostToolUse",
                 "tool_input": {"command": "git status"},
                 "tool_response": {"stdout": data, "stderr": "", "interrupted": False, "isImage": False}}
        reply = json.loads(claude_post_hook(json.dumps(event)))
        out = reply["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        self.assertLess(len(out), len(data))
        receipt_id = out.split("sbs show ")[1].split("]")[0]
        self.assertEqual(get_receipt(receipt_id), data)
        event["tool_input"]["command"] = "npm publish"
        self.assertEqual(claude_post_hook(json.dumps(event)), "")

    def test_codex_rewrite_preserves_other_input(self):
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                 "tool_input": {"command": "git diff --stat", "workdir": "/tmp", "timeout": 123}}
        reply = json.loads(codex_pre_hook(json.dumps(event)))
        updated = reply["hookSpecificOutput"]["updatedInput"]
        self.assertIn("stop_bullshiting shell", updated["command"])
        self.assertEqual(updated["workdir"], "/tmp")
        self.assertEqual(updated["timeout"], 123)
        event["tool_input"]["command"] = "git push"
        self.assertEqual(codex_pre_hook(json.dumps(event)), "")

    def test_install_uninstall_preserves_existing_hooks(self):
        root = Path(self.temp.name)
        settings = root / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        initial = {"permissions": {"deny": ["Bash(rm:*)"]},
                   "hooks": {"PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "echo ok"}]}]}}
        settings.write_text(json.dumps(initial))
        setup_agent("claude", root=root)
        setup_agent("claude", root=root)
        installed = json.loads(settings.read_text())
        self.assertEqual(len(installed["hooks"]["PostToolUse"]), 2)
        self.assertEqual(installed["permissions"], initial["permissions"])
        setup_agent("claude", remove=True, root=root)
        self.assertEqual(json.loads(settings.read_text()), initial)
        self.assertGreater(len(list(settings.parent.glob("*.backup-*"))), 0)

    def test_codex_hooks_location(self):
        root = Path(self.temp.name)
        setup_agent("codex", root=root)
        path = root / ".codex/hooks.json"
        doc = json.loads(path.read_text())
        self.assertEqual(doc["hooks"]["PreToolUse"][0]["matcher"], "Bash")
        setup_agent("codex", remove=True, root=root)
        self.assertEqual(json.loads(path.read_text()), {})

    def test_shell_aliases_installed_once(self):
        root = Path(self.temp.name)
        target = root / ".zshrc"
        target.write_text("export FOO=bar\n")
        setup_shell(root=root)
        setup_shell(root=root)
        text = target.read_text()
        self.assertEqual(text.count("# >>> STOP-BULLSHITING >>>"), 1)
        self.assertIn("export FOO=bar", text)
        setup_shell(root=root, remove=True)
        self.assertEqual(target.read_text(), "export FOO=bar\n\n")


if __name__ == "__main__":
    unittest.main()
