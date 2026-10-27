#!/usr/bin/env bash
# Install or repair the reviewed SBS 0.2.0 release without pipx or sudo.
# Existing SBS integrations are refreshed; new integrations remain opt-in.
set -eu
SBS_ENV="$HOME/.local/share/stop-bullshiting/venv"
if [ ! -e "$SBS_ENV" ]; then
  SBS_PYTHON=""
  for SBS_CANDIDATE in python3 python3.13 python3.12 python3.11 python3.10; do
    if "$SBS_CANDIDATE" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
      SBS_PYTHON="$SBS_CANDIDATE"
      break
    fi
  done
  if [ -z "$SBS_PYTHON" ]; then
    printf '%s\n' 'Installation arrêtée : Python 3.10 ou plus récent est nécessaire.'
    exit 1
  fi
  "$SBS_PYTHON" -m venv "$SBS_ENV"
fi
if [ ! -x "$SBS_ENV/bin/python" ]; then
  printf '%s\n' "Installation arrêtée : environnement incomplet dans $SBS_ENV." >&2
  exit 1
fi
"$SBS_ENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.prefix != sys.base_prefix else "Environnement SBS invalide : un venv Python 3.10+ est nécessaire.")'
"$SBS_ENV/bin/python" -m pip install --upgrade 'stop-bullshiting[tokens] @ https://github.com/searadacunha/Stop-Bullshiting/archive/b5d66813fc71df6768bb03404312ceb233f0d508.zip'
"$SBS_ENV/bin/python" - <<'PY'
from pathlib import Path
from datetime import datetime
import json, os, shutil
from stop_bullshiting import __version__
from stop_bullshiting.hooks import setup_agent, setup_shell, SHELL_START, SHELL_END

if __version__ != "0.2.0":
    raise SystemExit("La version attendue 0.2.0 n'a pas été installée.")
root = Path.home()
shell_root = Path(os.environ.get("ZDOTDIR") or root)
rc = shell_root / ".zshrc"
line = 'export PATH="$HOME/.local/share/stop-bullshiting/venv/bin:$PATH"'
text = rc.read_text() if rc.exists() else ""
if line not in text.splitlines():
    if rc.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(rc, str(rc) + ".backup-sbs-" + stamp)
    with rc.open("a") as stream:
        stream.write("\n" + line + "\n")
if SHELL_START in text and SHELL_END in text:
    print(setup_shell(root=shell_root))
for agent, rel, event in (
    ("claude", ".claude/settings.json", "PostToolUse"),
    ("codex", ".codex/hooks.json", "PreToolUse"),
):
    config = root / rel
    if not config.exists():
        continue
    try:
        groups = json.loads(config.read_text()).get("hooks", {}).get(event, [])
        installed = any(
            "stop_bullshiting" in str(hook.get("command", ""))
            for group in groups if isinstance(group, dict)
            for hook in group.get("hooks", []) if isinstance(hook, dict)
        )
        if installed:
            print(setup_agent(agent))
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(f"Intégration {agent} non actualisée : {error}")
print("Stop-Bullshiting " + __version__ + " installé.")
PY
"$SBS_ENV/bin/sbs" --version
printf '%s\n' "Ouvre un nouveau terminal pour charger le PATH et les lanceurs actualisés."
printf '%s\n' "Si le hook Codex a changé, vérifie /hooks dans Codex."
"$SBS_ENV/bin/sbs" bench --tokenizer o200k_base
