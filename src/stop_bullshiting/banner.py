"""Fast ANSI banner displayed when requested; never emitted from a JSON hook."""

from __future__ import annotations

import os
import sys

LOGO = r"""
  ███████╗████████╗ ██████╗ ██████╗
  ██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗
  ███████╗   ██║   ██║   ██║██████╔╝
  ╚════██║   ██║   ██║   ██║██╔═══╝
  ███████║   ██║   ╚██████╔╝██║
  ╚══════╝   ╚═╝    ╚═════╝ ╚═╝

  B U L L S H I T I N G
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LESS NOISE   •   MORE SIGNAL   •   LOCAL FIRST
"""


def banner(*, stream=None, color=True) -> None:
    stream = stream or sys.stderr
    if color and hasattr(stream, "isatty") and stream.isatty() and not os.getenv("NO_COLOR"):
        stream.write("\x1b[1;36m" + LOGO + "\x1b[0m")
    else:
        stream.write(LOGO)
    stream.flush()
