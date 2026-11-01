import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.hooks import (claude_post_hook, codex_pre_hook, run_hook, safe_read_command,
                                    setup_agent, setup_shell)
from stop_bullshiting.store import get_receipt, report


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": str(Path(self.temp.name)/"state"),
                                        "CODEX_HOME": str(Path(self.temp.name)/"codex"),
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
                    "git -c alias.x=status x", "rg --pre=curl foo",
                    "/tmp/ls -la", "./rg TODO .", "/opt/tools/git status",
                    "git diff --textconv", "git show --output=/tmp/result",
                    "rg password /etc", "rg pattern ../private", "ls -la /",
                    "grep token ~/secrets", "git show ../../private"):
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

    def _bash_event(self, command, stdout):
        return {"tool_name": "Bash", "hook_event_name": "PostToolUse", "tool_input": {"command": command},
                "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False,
                                  "noOutputExpected": False}}

    def test_compound_command_gets_its_command_view_and_keeps_response_shape(self):
        rows = [f"tests/test_mod.py::test_case_{i} PASSED [{i // 4:3d}%]" for i in range(400)]
        stdout = "\n".join(["collected 400 items"] + rows + ["===== 400 passed in 2.31s ====="])
        event = self._bash_event("cd /repo && python3 -m pytest -v 2>&1 | tail -500", stdout)
        replaced = json.loads(claude_post_hook(json.dumps(event)))["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(set(replaced), set(event["tool_response"]))
        self.assertIn("400 passed in 2.31s", replaced["stdout"])
        self.assertLess(len(replaced["stdout"]), len(stdout) / 5)
        self.assertEqual(get_receipt(replaced["stdout"].split("sbs show ")[1].split("]")[0]), stdout)

    def test_unknown_command_output_is_not_previewed(self):
        data = "\n".join(f"row {i} of a report the agent asked for" for i in range(400))
        for command in ("python3 report.py", "cat notes.txt", "cd x && cat a; cat b", "sed -n 1,400p f"):
            self.assertEqual(claude_post_hook(json.dumps(self._bash_event(command, data))), "", command)

    def test_terminal_noise_is_removed_from_any_command(self):
        stdout = "".join(f"\x1b[32mDownloading {i}%\x1b[0m\r" for i in range(900)) + "done\n"
        reply = claude_post_hook(json.dumps(self._bash_event("./install.sh", stdout)))
        out = json.loads(reply)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        self.assertIn("done", out)
        self.assertNotIn("\x1b", out)
        self.assertEqual(get_receipt(out.split("sbs show ")[1].split("]")[0]), stdout)

    def test_read_is_never_replaced_because_line_numbers_would_be_wrong(self):
        content = "\n".join(["2026-10-26 12:00:00 INFO worker tick"] * 600 + ["2026-10-26 12:00:01 ERROR E_LEDGER_9"])
        file = {"filePath": "/x/run.log", "content": content, "numLines": 601, "startLine": 1, "totalLines": 601}
        event = {"tool_name": "Read", "hook_event_name": "PostToolUse", "tool_input": {"file_path": "/x/run.log"},
                 "tool_response": {"type": "text", "file": file}}
        self.assertEqual(claude_post_hook(json.dumps(event)), "")

    def test_grep_content_is_grouped_without_losing_matches(self):
        content = "\n".join(f"src/deep/module/path/file_{i % 3}.py:{i}:value = find_me({i})" for i in range(200))
        event = {"tool_name": "Grep", "hook_event_name": "PostToolUse", "tool_input": {"pattern": "find_me"},
                 "tool_response": {"mode": "content", "numFiles": 0, "filenames": [], "content": content,
                                   "numLines": 200, "totalLines": 200}}
        replaced = json.loads(claude_post_hook(json.dumps(event)))["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(replaced["numLines"], 200)
        self.assertLess(len(replaced["content"]), len(content))
        for index in range(3):
            self.assertIn(f"src/deep/module/path/file_{index}.py", replaced["content"])

    def test_hook_records_coverage_without_content(self):
        claude_post_hook(json.dumps(self._bash_event("echo secret-value", "secret-value\n")))
        events = report()["events"]
        self.assertEqual([(e["source"], e["outcome"], e["calls"]) for e in events], [("claude:Bash", "unchanged", 1)])
        self.assertNotIn("secret-value", json.dumps(report()))

    def test_codex_never_hides_a_command_from_an_exec_policy_rule(self):
        rules = Path(self.temp.name) / "codex" / "rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text('prefix_rule(pattern=["git", "log"], decision="forbidden")\n')
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git log -1"}}
        self.assertEqual(codex_pre_hook(json.dumps(event)), "")
        event["tool_input"]["command"] = "ls -la"
        self.assertIn("stop_bullshiting shell", codex_pre_hook(json.dumps(event)))
        project = Path(self.temp.name) / "project"
        (project / ".codex" / "rules").mkdir(parents=True)
        (project / ".codex" / "rules" / "x.rules").write_text("prefix_rule(pattern=['ls'], decision='prompt')\n")
        event["cwd"] = str(project)
        self.assertEqual(codex_pre_hook(json.dumps(event)), "")

    def test_hook_failure_is_silent(self):
        for agent in ("claude", "codex"):
            for raw in ("", "not json", "[]", '{"tool_name": 3}', '{"hook_event_name":"PostToolUse","tool_name":"Bash",'
                        '"tool_input":[],"tool_response":"x"}'):
                self.assertEqual(run_hook(agent, raw), "")
        with patch("stop_bullshiting.hooks.claude_post_hook", side_effect=RuntimeError("boom")):
            self.assertEqual(run_hook("claude", "{}"), "")

    def test_commands_needing_a_shell_are_not_wrapped(self):
        for command in ("ls *.md", "rg foo src/*.py", "git diff -- *.json", "git status #note", "ls [ab].txt",
                        "ls file?.txt", "ls =ls", "git log !x"):
            self.assertFalse(safe_read_command(command), command)
            event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
            self.assertEqual(codex_pre_hook(json.dumps(event)), "", command)

    def test_rg_cannot_run_a_program_through_an_allowlisted_flag(self):
        self.assertFalse(safe_read_command("rg --hostname-bin=./evil --hyperlink-format=default x"))

    def test_failed_claude_output_remains_untouched(self):
        for exit_key in ("exitCode", "exit_code", "returncode"):
            with self.subTest(exit_key=exit_key):
                event = {"tool_name": "Bash", "hook_event_name": "PostToolUse",
                         "tool_input": {"command": "git status"},
                         "tool_response": {"stdout": "diagnostic\n" * 500, exit_key: 7}}
                self.assertEqual(claude_post_hook(json.dumps(event)), "")

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

    def test_claude_matcher_covers_replaceable_tools(self):
        root = Path(self.temp.name)
        setup_agent("claude", root=root)
        group = json.loads((root / ".claude/settings.json").read_text())["hooks"]["PostToolUse"][0]
        self.assertEqual(group["matcher"], "Bash|Grep")

    def test_codex_hooks_location(self):
        root = Path(self.temp.name)
        setup_agent("codex", root=root)
        path = root / ".codex/hooks.json"
        doc = json.loads(path.read_text())
        self.assertEqual(doc["hooks"]["PreToolUse"][0]["matcher"], "Bash")
        setup_agent("codex", remove=True, root=root)
        self.assertEqual(json.loads(path.read_text()), {})

    def test_mixed_group_preserves_neighboring_hooks(self):
        root = Path(self.temp.name)
        settings = root / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        neighbor = {"type": "command", "command": "echo another-hook", "timeout": 20}
        group = {"matcher": "Bash", "description": "user configuration", "hooks": [
            {"type": "command", "command": "python -m stop_bullshiting hook claude"}, neighbor]}
        settings.write_text(json.dumps({"hooks": {"PostToolUse": [group]}}))
        setup_agent("claude", root=root)
        groups = json.loads(settings.read_text())["hooks"]["PostToolUse"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0], {**group, "hooks": [neighbor]})
        setup_agent("claude", remove=True, root=root)
        groups = json.loads(settings.read_text())["hooks"]["PostToolUse"]
        self.assertEqual(groups, [{**group, "hooks": [neighbor]}])

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

    def test_shell_launcher_uses_installed_interpreter(self):
        root = Path(self.temp.name)
        interpreter = "/tmp/example environment/bin/python"
        with patch("stop_bullshiting.hooks.sys.executable", interpreter):
            setup_shell(root=root)
        snippet = (root / ".zshrc").read_text()
        for agent in ("claude", "codex"):
            self.assertIn(f"command {shlex.quote(interpreter)} -m stop_bullshiting launch {agent}", snippet)
        self.assertNotIn("command sbs", snippet)


if __name__ == "__main__":
    unittest.main()
