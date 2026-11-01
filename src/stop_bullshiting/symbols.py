"""Small, dependency-free source-code locator: symbols, references and exact excerpts.

Python goes through its AST and JavaScript/TypeScript through ``jsparse``. Both
find names, not meaning: a reference is any occurrence of the identifier outside
strings and comments, so shadowed or repeated names are reported too.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

from .jsparse import SUFFIXES as JS_SUFFIXES, identifiers as js_identifiers, js_symbols

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__", ".tox", ".next"}
CODE_SUFFIXES = {".py"} | JS_SUFFIXES
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
            # Do not follow in-workspace symlinked files into another tree.
            if path.is_symlink():
                continue
            if path.suffix not in CODE_SUFFIXES and path.suffix not in (".go", ".rs", ".md"):
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
    source = text.splitlines()

    def walk(nodes, prefix=""):
        for item in nodes:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + item.name
                kind = "class" if isinstance(item, ast.ClassDef) else "async" if isinstance(item, ast.AsyncFunctionDef) else "def"
                output.append({"name": name, "kind": kind, "line": item.lineno,
                               "end": getattr(item, "end_lineno", item.lineno),
                               "signature": source[item.lineno - 1].strip()[:120]})
                walk(getattr(item, "body", []), name + ".")
    walk(tree.body)
    return output


def _source(file: Path) -> str | None:
    try:
        if file.stat().st_size > MAX_SOURCE_BYTES:
            return None
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def file_symbols(file: Path) -> list[dict]:
    """Symbols of one source file in its language; unsupported files have none."""
    if file.suffix == ".py":
        return python_symbols(file)
    if file.suffix in JS_SUFFIXES:
        text = _source(file)
        try:
            return js_symbols(text) if text is not None else []
        except RecursionError:  # absurdly nested namespaces: skip the file like a syntax error
            return []
    return []


def file_identifiers(file: Path) -> dict[str, list[int]]:
    """Map each identifier used in a file to its lines, ignoring strings and comments."""
    text = _source(file)
    if text is None:
        return {}
    if file.suffix in JS_SUFFIXES:
        return js_identifiers(text)
    if file.suffix != ".py":
        return {}
    try:
        tree = ast.parse(text, filename=str(file))
    except (SyntaxError, ValueError, RecursionError):
        return {}
    found: dict[str, list[int]] = {}

    def add(name: str, line: int) -> None:
        found.setdefault(name, []).append(line)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            add(node.id, node.lineno)
        elif isinstance(node, ast.Attribute):
            add(node.attr, node.end_lineno or node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                add(alias.name.rsplit(".", 1)[-1], node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node.name, node.lineno)
    return found


def _label(file: Path, root: Path) -> str:
    return str(file.relative_to(root)) if root.is_dir() else file.name


def _matches(symbol: str, wanted: str) -> bool:
    return symbol == wanted or symbol.rsplit(".", 1)[-1] == wanted or symbol.endswith("." + wanted)


def definitions(name: str, root: Path, *, limit: int = 50) -> list[dict]:
    """Find declarations by exact name, or by the trailing part of a dotted name."""
    root = root.expanduser().resolve()
    found: list[dict] = []
    identifier = name.rsplit(".", 1)[-1]
    for file in _iterate(root):
        # Parsing is the slow part, and a file that lacks the word cannot declare it.
        text = _source(file) if file.suffix in CODE_SUFFIXES else None
        if text is None or identifier not in text:
            continue
        for sym in file_symbols(file):
            if _matches(sym["name"], name):
                found.append({**sym, "path": _label(file, root), "file": file})
                if len(found) >= limit:
                    return found
    return found


def references(name: str, root: Path, *, limit: int = 100) -> list[str]:
    """Lines naming an identifier, without its definition lines. A name match, not resolution."""
    root = root.expanduser().resolve()
    identifier = name.rsplit(".", 1)[-1]
    output: list[str] = []
    for file in _iterate(root):
        if file.suffix not in CODE_SUFFIXES:
            continue
        text = _source(file)
        if text is None or identifier not in text:
            continue
        lines = file_identifiers(file).get(identifier)
        if not lines:
            continue
        declared = {sym["line"] for sym in file_symbols(file) if _matches(sym["name"], identifier)}
        source = text.splitlines()
        for line in sorted(set(lines) - declared):
            content = source[line - 1].strip() if line <= len(source) else ""
            output.append(f"{_label(file, root)}:{line}: {content[:200]}")
            if len(output) >= limit:
                return output
    return output


def symbol_source(name: str, root: Path, *, max_chars: int = 20000) -> str:
    """Exact numbered source of matching declarations, cut only between symbols."""
    found = definitions(name, root)
    sections: list[str] = []
    used = 0
    for index, sym in enumerate(found):
        lines = sym["end"] - sym["line"] + 1
        header = f"{sym['path']}:{sym['line']}-{sym['end']} {sym['kind']} {sym['name']}"
        body = excerpt(sym["file"], sym["line"], sym["end"], max_lines=max(lines, 1))
        section = f"{header}\n{body}"
        if used + len(section) > max_chars:
            remaining = len(found) - index
            sections.append(f"[SBS: {remaining} more match(es) over budget, starting at {header}. "
                            f"Read it in parts of up to 250 lines: sbs read {sym['path']} {sym['line']} "
                            f"{min(sym['end'], sym['line'] + 249)}]")
            break
        sections.append(section)
        used += len(section)
    return "\n\n".join(sections)


def symbol_map(root: Path, *, name_filter: str = "", limit: int = 160) -> list[str]:
    root = root.expanduser().resolve()
    out: list[str] = []
    for file in _iterate(root):
        if file.suffix not in CODE_SUFFIXES:
            continue
        for sym in file_symbols(file):
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
    # newline="" keeps CRLF line endings: this text may be matched against the file.
    with path.expanduser().open(encoding="utf-8", errors="replace", newline="") as source:
        for number, line in enumerate(source, 1):
            if number > end:
                break
            if number >= start:
                lines.append(f"{number:5}: {line.rstrip(chr(10))}")
    return "\n".join(lines)
