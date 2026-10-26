"""Environment configuration: no network services and no account credentials."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "stop-bullshiting"


def home() -> Path:
    override = os.environ.get("SBS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / APP_NAME


def min_chars() -> int:
    return max(0, int(os.environ.get("SBS_MIN_CHARS", "1400")))


def max_chars() -> int:
    return max(300, int(os.environ.get("SBS_MAX_CHARS", "3500")))


def min_gain() -> float:
    return min(0.95, max(0.01, float(os.environ.get("SBS_MIN_GAIN", "0.12"))))


def ensure_private_dir(directory: Path) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(directory, 0o700)
    return directory
