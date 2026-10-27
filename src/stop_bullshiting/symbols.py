"""Small, dependency-free source-code locator. Python AST + targeted text search."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__", ".tox", ".next"}
MAX_FILES = 4000
MAX_SOURCE_BYTES = 600_000


def _iterate(root: Path):
    count = 0
    if root.is_file():
        yield root
        return
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".cache"))
        for file in sorted(files):
            if count >= MAX_FILES:
                return
            path = Path(folder) / file
            if path.suffix not in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".md"):
                continue
            count += 1
            yield path


def python_symbols(file: Path) -> list[dict]:
    """Return real AST positions; skip syntactically invalid files safely."""
    if file.suffix != ".py" or file.stat().st_size > MAX_SOURCE_BYTES:
        return []
    try:
        text = file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(file))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return []
    output: list[dict] = []

    def walk(nodes, prefix=""):
        for item in nodes:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + item.name
                kind = "class" if isinstance(item, ast.ClassDef) else "async" if isinstance(item, ast.AsyncFunctionDef) else "def"
                output.append({"name": name, "kind": kind, "line": item.lineno,
                               "end": getattr(item, "end_lineno", item.lineno)})
                walk(getattr(item, "body", []), name + ".")
    walk(tree.body)
    return output


def symbol_map(root: Path, *, name_filter: str = "", limit: int = 160) -> list[str]:
    root = root.expanduser().resolve()
    out: list[str] = []
    for file in _iterate(root):
        if file.suffix != ".py":
            continue
        for sym in python_symbols(file):
            if name_filter.casefold() not in sym["name"].casefold():
                continue
            label = str(file.relative_to(root)) if root.is_dir() else file.name
            out.append(f"{label}:{sym['line']}-{sym['end']} {sym['kind']} {sym['name']}")
            if len(out) >= limit:
                return out
    return out


def search(query: str, root: Path, *, limit: int = 50) -> list[str]:
    """Case-sensitive literal search; never interpolate input into a shell command."""
    if limit < 1:
        raise ValueError("Search limit must be at least 1")
    root = root.expanduser().resolve()
    rg = shutil.which("rg")
    if rg:
        argv = [rg, "--no-heading", "-n", "--color", "never", "-F", "--max-count", "5", "--glob", "!.git/**",
                "--glob", "!node_modules/**", "--", query, str(root)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=15)
            if proc.returncode in (0, 1):
                return proc.stdout.splitlines()[:limit]
        except (OSError, subprocess.TimeoutExpired):
            pass
    results: list[str] = []
    for file in _iterate(root):
        try:
            if file.stat().st_size > MAX_SOURCE_BYTES:
                continue
            with file.open(encoding="utf-8", errors="replace") as source:
                for number, line in enumerate(source, 1):
                    if query in line:
                        results.append(f"{file}:{number}:{line.rstrip()[:300]}")
                        if len(results) >= limit:
                            return results
        except OSError:
            continue
    return results


def excerpt(path: Path, start: int, end: int, *, max_lines: int = 250) -> str:
    """Exact, line-numbered source excerpts: no compression or content mutation."""
    if start < 1 or end < start or end - start + 1 > max_lines:
        raise ValueError(f"Invalid range; request no more than {max_lines} lines")
    lines: list[str] = []
    with path.expanduser().open(encoding="utf-8", errors="replace") as source:
        for number, line in enumerate(source, 1):
            if number > end:
                break
            if number >= start:
                lines.append(f"{number:5}: {line.rstrip()}")
    return "\n".join(lines)
