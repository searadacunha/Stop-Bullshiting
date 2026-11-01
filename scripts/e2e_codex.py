#!/usr/bin/env python3
"""Exercise a hook with the real installed Codex binary, without any account.

A throwaway CODEX_HOME points Codex at a scripted local model server, so no
credentials, user configuration or quota are involved. The hook is approved the
normal way (hooks/list, then trusted_hash in the throwaway config). The server
records the function_call_output the model would have received.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

USAGE = {"input_tokens": 0, "input_tokens_details": None, "output_tokens": 0,
         "output_tokens_details": None, "total_tokens": 0}


def scripted_server(command: str, log: list[dict]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self._send(b'{"data":[],"models":[]}', "application/json")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            outputs = [item for item in body.get("input", [])
                       if isinstance(item, dict) and item.get("type") == "function_call_output"]
            log.append({"tools": [t.get("name") for t in body.get("tools", [])], "outputs": outputs})
            if outputs:
                item = {"type": "message", "role": "assistant", "id": "m1",
                        "content": [{"type": "output_text", "text": "done"}]}
            else:
                names = [t.get("name") for t in body.get("tools", [])]
                if "exec_command" in names:
                    name, arguments = "exec_command", {"cmd": command}
                else:
                    name, arguments = "shell_command", {"command": command}
                item = {"type": "function_call", "call_id": "call1", "name": name,
                        "arguments": json.dumps(arguments)}
            events = [{"type": "response.created", "response": {"id": "r1"}},
                      {"type": "response.output_item.done", "item": item},
                      {"type": "response.completed", "response": {"id": "r1", "usage": USAGE}}]
            payload = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
            self._send(payload.encode(), "text/event-stream")

        def _send(self, payload: bytes, kind: str):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def rpc(codex: str, env: dict, cwd: Path) -> list[dict]:
    """Ask Codex itself for the hook key and hash, as `/hooks` would."""
    messages = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "sbs-e2e", "version": "0"}}},
        {"method": "initialized"},
        {"id": 2, "method": "hooks/list", "params": {"cwds": [str(cwd)]}},
    ]
    proc = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, cwd=cwd)
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        for line in proc.stdout:
            reply = json.loads(line)
            if reply.get("id") == 2:
                if "error" in reply:
                    raise RuntimeError(f"hooks/list failed: {reply['error']}")
                found = []
                for entry in reply["result"].get("data", []):
                    found.extend(entry.get("hooks", []))
                return found
    finally:
        proc.kill()
    return []


def run(hook_command: str | None, event: str, command: str, approval: str,
        sandbox: str = "read-only", rule: str | None = None) -> dict:
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("codex is not installed")
    log: list[dict] = []
    server = scripted_server(command, log)
    with tempfile.TemporaryDirectory(prefix="sbs-e2e-codex-") as raw:
        root = Path(raw).resolve()
        home, project = root / "home", root / "project"
        home.mkdir()
        project.mkdir()
        (project / "numbers.txt").write_text("".join(f"{i}\n" for i in range(1, 41)))
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        config = (f'model = "gpt-5.1-codex"\nmodel_provider = "scripted"\n'
                  f'[model_providers.scripted]\nname = "scripted"\n'
                  f'base_url = "http://127.0.0.1:{server.server_port}/v1"\nwire_api = "responses"\n')
        (home / "config.toml").write_text(config)
        if rule:
            (home / "rules").mkdir()
            (home / "rules" / "default.rules").write_text(rule + "\n")
        env = {**os.environ, "CODEX_HOME": str(home)}
        env.pop("OPENAI_API_KEY", None)
        hooks = []
        if hook_command:
            (home / "hooks.json").write_text(json.dumps({"hooks": {event: [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": hook_command, "timeout": 20}]}]}}))
            hooks = rpc(codex, env, project)
            if not hooks:
                raise RuntimeError("Codex listed no hook")
            with (home / "config.toml").open("a") as stream:
                stream.write("[hooks.state]\n")
                for hook in hooks:
                    stream.write(f'[hooks.state."{hook["key"]}"]\ntrusted_hash = "{hook["currentHash"]}"\n')
        proc = subprocess.run(
            [codex, "-a", approval, "exec", "--ephemeral", "--json", "--skip-git-repo-check",
             "--sandbox", sandbox, "-C", str(project), "run the scripted step"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=120)
    server.shutdown()
    outputs = [o for request in log for o in request["outputs"]]
    return {"codex_exit": proc.returncode, "hooks_listed": len(hooks),
            "model_visible_output": outputs[0].get("output") if outputs else None,
            "stderr_tail": proc.stderr[-400:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", help="Hook command Codex will run; omit for a control run")
    parser.add_argument("--event", default="PostToolUse", choices=["PreToolUse", "PostToolUse"])
    parser.add_argument("--command", default="seq 1 40", help="Shell command the scripted model requests")
    parser.add_argument("--approval", default="never", help="Codex approval policy for the run")
    parser.add_argument("--sandbox", default="read-only", help="Codex sandbox mode for the run")
    parser.add_argument("--rule", help="Exec-policy rule text to place in the throwaway home")
    args = parser.parse_args()
    print(json.dumps(run(args.hook, args.event, args.command, args.approval, args.sandbox, args.rule),
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
