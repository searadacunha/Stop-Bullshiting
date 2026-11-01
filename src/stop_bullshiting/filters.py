"""Command-aware views of successful tool output.

A filter returns a smaller view or ``None``. ``None`` means "not recognized" and
leaves the decision to the caller: an unfamiliar format is never guessed at.
Callers only use these views on output whose complete original is stored, and
only on commands that succeeded.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePosixPath

TEST_RUNNERS = {"pytest", "py.test", "unittest", "nose2", "jest", "vitest", "mocha"}
SEARCHERS = {"rg", "grep", "egrep", "fgrep", "ag", "ack"}
WRAPPERS = (("rtk", "proxy"), ("rtk",), ("command",), ("time",), ("uv", "run"), ("poetry", "run"),
            ("pipenv", "run"), ("pnpm", "exec"), ("yarn", "exec"), ("npx",), ("bunx",))
GENERATED = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock",
             "Gemfile.lock", "composer.lock", "go.sum", "bun.lock"}
GENERATED_SUFFIXES = (".min.js", ".min.css", ".js.map", ".css.map")

# Paired trials: models almost never fetched an omitted line back, and answered wrongly
# instead. So a view drops redundant text, not items, unless it would exceed what an
# agent's own output limit cuts anyway (30,000 characters by default in Claude Code).
ITEM_BUDGET = 30000

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
JSON_SPACE = re.compile(r'("(?:[^"\\]|\\.)*")|[ \t\r\n]+')
PASSING_LINE = re.compile(
    r"""^\s*(?:
        \S+::\S.*\sPASSED(?:\s+\[\s*\d+%\])?     # pytest -v
      | \S+\.py\s+\.+\s*(?:\[\s*\d+%\])?         # pytest progress row without failures
      | \.+\s*(?:\[\s*\d+%\])?                   # bare progress dots
      | .*\s\.\.\.\s+ok                          # unittest -v, cargo test
      | [✓✔√]\s.*                                # jest, vitest, mocha
      | PASS\s+\S.*                              # jest file row
      | ---\s+PASS:\s.*                          # go test -v
      | ===\s+(?:RUN|PAUSE|CONT|NAME)\s.*        # go test -v
      | PASS                                     # go test
    )\s*$""",
    re.VERBOSE,
)
TEST_SUMMARY = re.compile(
    r"(?m)^\s*(?:=+ .*\b(?:passed|failed|errors?|skipped|xfailed)\b.* =+"
    r"|\d+ (?:passed|failed)\b.*|Ran \d+ tests? in .*|Tests?:\s+\d.*|Test Files\s+\d.*"
    r"|test result: .*|ok\s+\S+\s+.*|\d+ passing\b.*|Tests\s+\d.*)\s*$"
)
TEST_FAILURE = re.compile(
    r"(?m)^\s*(?:FAIL(?:ED)?\b|ERROR\b|--- FAIL|Traceback \(most|[✕✗×]\s)|\b[1-9]\d* (?:failed|errors?)\b"
    r"|\bFAILED\b|test result: FAILED"
)
HINT_LINE = re.compile(r'^\s*\((?:use "git |commit or discard|fix conflicts|all conflicts)')
LOG_COMMIT = re.compile(r"^commit ([0-9a-f]{7,64})(?: (.*))?$")
GREP_LINE = re.compile(r"^(?P<path>[^\s:][^:\n]*):(?P<line>\d+):(?P<text>.*)$")


def _shell_tokens(command: str) -> list[str] | None:
    # A merged stderr does not change what the command prints, unlike the other operators.
    text = re.sub(r"\s\d?>&\d(?=\s|$)", " ", command)
    if "`" in text or "\n" in text:
        return None
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def producer(command: str) -> list[str] | None:
    """Return the argv whose stdout is the whole output, or None when unsure.

    Pipes, redirections and subshells mean stdout is no longer that program's own
    format, and any earlier printing step mixes two formats.
    """
    tokens = _shell_tokens(command) if isinstance(command, str) else None
    if not tokens:
        return None
    segments: list[list[str]] = [[]]
    # head and tail only keep a subset of whole lines, so the format is unchanged.
    while "|" in tokens and tokens[tokens.index("|") + 1:][:1] in (["head"], ["tail"]):
        cut = len(tokens) - 1 - tokens[::-1].index("|")
        if tokens[cut + 1:][:1] not in (["head"], ["tail"]) or any(
                token.startswith(("-c", "-f", "--bytes", "--follow")) or set(token) <= set("|&;<>()")
                for token in tokens[cut + 2:]):
            return None
        tokens = tokens[:cut]
    for token in tokens:
        if token in ("&&", ";"):
            segments.append([])
        elif set(token) <= set("|&;<>()"):
            return None
        else:
            segments[-1].append(token)
    segments = [segment for segment in segments if segment]
    if not segments:
        return None
    assignment = re.compile(r"^[A-Za-z_]\w*=")
    for segment in segments[:-1]:
        if segment[0] not in ("cd", "pushd", "export", "source", ".") and not all(
                assignment.match(token) for token in segment):
            return None
    argv = segments[-1]
    while argv and assignment.match(argv[0]):
        argv = argv[1:]
    stripped = True
    while stripped:
        stripped = False
        for wrapper in WRAPPERS:
            if tuple(argv[:len(wrapper)]) == wrapper and len(argv) > len(wrapper):
                argv = argv[len(wrapper):]
                stripped = True
                break
    if not argv:
        return None
    return [PurePosixPath(argv[0]).name] + argv[1:]


def classify(command: str) -> str | None:
    """Name the output format a command prints; None leaves its output alone."""
    argv = producer(command)
    if not argv:
        return None
    program, args = argv[0], argv[1:]
    if re.fullmatch(r"python[\d.]*", program) and len(args) >= 2 and args[0] == "-m":
        program, args = args[1], args[2:]
    if program == "git":
        while args and args[0].startswith("-"):
            args = args[2:] if args[0] in ("-C", "-c", "--git-dir", "--work-tree") else args[1:]
        if args[:1] == ["show"] and any(":" in arg for arg in args[1:]):
            return None  # git show REV:path prints a file, not a patch
        return {"status": "git-status", "log": "git-log", "diff": "git-diff", "show": "git-diff",
                "grep": "search"}.get(args[0]) if args else None
    if program in SEARCHERS:
        return "search"
    if program in TEST_RUNNERS:
        return "tests"
    if program in ("npm", "pnpm", "yarn", "bun"):
        runs_test = args[:1] == ["test"] or args[:1] == ["t"] or (
            args[:1] in (["run"], ["run-script"]) and len(args) > 1 and args[1].startswith("test"))
        return "tests" if runs_test else None
    if program in ("go", "cargo") and args[:1] == ["test"]:
        return "tests"
    return None


def clean(text: str) -> str:
    """Remove what a reader cannot see: colors, overwritten progress, repeats, JSON spacing."""
    text = ANSI.sub("", text)
    output: list[str] = []
    run = 0
    for raw in text.split("\n"):
        line = raw
        body = raw.rstrip("\r")
        if "\r" in body:
            # A terminal shows only what follows the last carriage return.
            line = body.rsplit("\r", 1)[-1] + raw[len(body):]
        if output and line == output[-1] and line.strip():
            run += 1
            continue
        if run:
            output.extend([output[-1]] * run if run < 2 else [f"... [previous line repeated {run} more times]"])
            run = 0
        output.append(line)
    if run:
        output.extend([output[-1]] * run if run < 2 else [f"... [previous line repeated {run} more times]"])
    return minify_json("\n".join(output))


def minify_json(text: str) -> str:
    """Drop JSON whitespace outside strings; numbers and key order stay as written."""
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return text
    packed = JSON_SPACE.sub(lambda match: match.group(1) or "", stripped)
    try:
        if json.loads(packed) != json.loads(stripped):
            return text
    except (ValueError, RecursionError):
        return text
    return packed


def _fit(lines: list[str], budget: int | None) -> list[str]:
    """Keep the start and end of a list too long for the budget, saying how much is gone."""
    if budget is None or sum(len(line) + 1 for line in lines) <= budget:
        return lines
    head: list[str] = []
    tail: list[str] = []
    used = 60
    front, back = 0, len(lines) - 1
    while front <= back:
        if len(head) <= 2 * len(tail):
            cost = len(lines[front]) + 1
            if used + cost > budget:
                break
            head.append(lines[front])
            front += 1
        else:
            cost = len(lines[back]) + 1
            if used + cost > budget:
                break
            tail.append(lines[back])
            back -= 1
        used += cost
    omitted = back - front + 1
    if omitted <= 0:
        return lines
    return head + [f"... [{omitted} entries NOT shown; search them: sbs find WORDS] ..."] + tail[::-1]


def tests_view(text: str, budget: int | None = None) -> str | None:
    """Drop per-test success lines. Everything else stays verbatim, so does the summary."""
    # `pytest | tail` exits 0 even when tests fail, and a failing run is left whole.
    if not TEST_SUMMARY.search(text) or TEST_FAILURE.search(text):
        return None
    kept: list[str] = []
    dropped = 0
    for line in text.split("\n"):
        if PASSING_LINE.match(line):
            dropped += 1
        else:
            kept.append(line)
    if not dropped:
        return None
    return "\n".join(kept).strip("\n") + f"\n[SBS tests: {dropped} passing-test lines omitted]"


def git_status_view(text: str, budget: int | None = None) -> str | None:
    """Drop usage hints and fold long untracked lists; tracked paths always stay."""
    long_format = text.startswith(("On branch", "HEAD detached", "rebase in progress")) or "\nOn branch" in text[:200]
    untracked: list[str] = []
    kept: list[str] = []
    inside = False
    for line in text.split("\n"):
        if long_format:
            if not line.strip() or HINT_LINE.match(line):
                continue
            if line.startswith("Untracked files:"):
                inside = True
                kept.append(line)
                continue
            if inside and line.startswith("\t"):
                untracked.append(line.strip())
                continue
            inside = False
            kept.append(line)
        elif line.startswith("?? "):
            untracked.append(line[3:])
        elif line.strip():
            kept.append(line)
    if len(untracked) <= 30:
        rendered = [("\t" if long_format else "?? ") + path for path in untracked]
    else:
        groups: dict[str, int] = {}
        for path in untracked:
            parts = path.strip('"').split("/")
            key = "/".join(parts[:2]) + ("/" if len(parts) > 2 else "") if len(parts) > 1 else "(top level)"
            groups[key] = groups.get(key, 0) + 1
        rendered = [("\t" if long_format else "?? ") + f"{key} ({count} untracked)" for key, count in sorted(groups.items())]
    if long_format:
        index = next((i for i, line in enumerate(kept) if line.startswith("Untracked files:")), len(kept))
        kept[index + 1:index + 1] = rendered
    else:
        kept.extend(rendered)
    return "\n".join(kept)


def git_log_view(text: str, budget: int | None = None) -> str | None:
    """One line per commit from the default format; any other format is left alone."""
    rows: list[str] = []
    current: dict | None = None

    def finish() -> None:
        if current is None:
            return
        extra = f" [+{current['body']} body lines]" if current["body"] else ""
        label = f" {current['refs']}" if current["refs"] else ""
        rows.append(f"{current['sha']}{label} | {current['date']} | {current['author']} | {current['subject']}{extra}")

    for line in text.split("\n"):
        commit = LOG_COMMIT.match(line)
        if commit:
            finish()
            current = {"sha": commit.group(1), "refs": commit.group(2) or "", "date": "", "author": "",
                       "subject": "", "body": 0}
        elif current is None:
            if line.strip():
                return None
        elif line.startswith("Author: "):
            current["author"] = line[8:].strip()
        elif line.startswith(("Date: ", "AuthorDate: ")):
            current["date"] = line.split(":", 1)[1].strip()
        elif line.startswith(("Merge: ", "Commit: ", "CommitDate: ")):
            continue
        elif line.startswith("    "):
            if not current["subject"]:
                current["subject"] = line.strip()
            elif line.strip():
                current["body"] += 1
        elif line.strip():
            return None  # patches, stats and notes are evidence: not this view's to drop
    finish()
    if not rows or not all(" | " in row for row in rows):
        return None
    return "\n".join(_fit(rows, budget))


def _generated(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in GENERATED or name.endswith(GENERATED_SUFFIXES)


def git_diff_view(text: str, budget: int | None = None) -> str | None:
    """Fold hunks of generated files only. Source hunks are returned untouched."""
    output: list[str] = []
    folded = 0
    skipping = False
    added = removed = hidden = 0

    def flush() -> None:
        nonlocal skipping, added, removed, hidden
        if skipping and hidden:
            output.append(f"[SBS diff: generated file, {hidden} lines omitted, +{added} -{removed}]")
        skipping = False
        added = removed = hidden = 0

    for line in text.split("\n"):
        if line.startswith("diff --git "):
            flush()
            output.append(line)
            match = re.match(r'diff --git "?a/(.*?)"? "?b/(.*?)"?$', line)
            if match and _generated(match.group(2)):
                skipping = True
                folded += 1
            continue
        if skipping and (line.startswith("@@ ") or (hidden and line[:1] in ("+", "-", " ", "\\"))):
            hidden += 1
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
            continue
        if hidden:
            flush()  # anything else is not this file's hunk: another commit or another file
        output.append(line)
    flush()
    return "\n".join(output) if folded else None


def search_view(text: str, budget: int | None = None) -> str | None:
    """Print each path once. Every file and its match count survives any budget."""
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    groups: dict[str, list[str]] = {}
    for line in lines:
        match = GREP_LINE.match(line)
        if not match:
            return None  # context rows, headings and pathless output stay unchanged
        groups.setdefault(match.group("path"), []).append(f"{match.group('line')}:{match.group('text')}")
    if len(groups) < 1 or len(groups) == len(lines):
        return None

    def render(cap: int | None) -> str:
        output: list[str] = []
        for path, rows in groups.items():
            output.append(path if cap is None else f"{path} ({len(rows)} matches)")
            shown = rows if cap is None else rows[:cap]
            output.extend("  " + row for row in shown)
            if len(shown) < len(rows):
                output.append(f"  ... +{len(rows) - len(shown)} more in this file, NOT shown; search them: sbs find WORDS")
        return "\n".join(output)

    view = render(None)
    if budget is None or len(view) <= budget:
        return view
    for cap in (20, 10, 5, 3, 1, 0):
        view = render(cap)
        if len(view) <= budget:
            return view
    return None


VIEWS = {"tests": tests_view, "git-status": git_status_view, "git-log": git_log_view,
         "git-diff": git_diff_view, "search": search_view}


def command_view(command: str, text: str, budget: int | None = None) -> tuple[str, str] | None:
    """Return (format name, view) for a recognized command's output, else None."""
    kind = classify(command)
    if kind is None:
        return None
    cleaned = clean(text) if ("\x1b" in text or "\r" in text) else text
    view = VIEWS[kind](cleaned, ITEM_BUDGET if budget is None else budget)
    return (kind, view) if view else None
