"""JavaScript and TypeScript declarations from a small tokenizer, with no dependencies.

It skips strings, comments, template literals and regular expressions, then reads
declarations at module scope and in class bodies. It does not type-check, resolve
names or enter function bodies. Unusual syntax can hide a symbol; it should not
invent one.
"""

from __future__ import annotations

import re

SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}

IDENT = re.compile(r"[A-Za-z_$#\u0080-\uffff][\w$\u0080-\uffff]*")
NUMBER = re.compile(r"\d[\w.]*")
REGEX_AFTER_WORD = {"return", "typeof", "case", "in", "of", "do", "else", "void", "delete", "throw",
                    "new", "yield", "await", "instanceof"}
KEYWORDS = REGEX_AFTER_WORD | {
    "abstract", "as", "async", "break", "catch", "class", "const", "continue", "debugger", "declare",
    "default", "enum", "export", "extends", "false", "finally", "for", "from", "function", "get", "if",
    "implements", "import", "interface", "let", "module", "namespace", "null", "override", "private",
    "protected", "public", "readonly", "satisfies", "set", "static", "super", "switch", "this", "true",
    "try", "type", "undefined", "var", "while", "with"}
MODIFIERS = {"export", "default", "declare", "abstract", "async", "public", "private", "protected",
             "static", "readonly", "override", "accessor"}
STATEMENT_START = {"const", "let", "var", "function", "class", "export", "import", "interface", "type",
                   "enum", "if", "for", "while", "return", "switch", "try", "throw", "async", "declare",
                   "abstract", "namespace", "module"}
CONTINUES_LINE = {"=", "=>", ",", "+", "-", "*", "/", "%", "&", "|", "^", "?", ":", "<", ">", ".", "!", "~",
                  "(", "[", "{"}
OPENERS = {"(": ")", "[": "]", "{": "}"}

Token = tuple  # (kind, value, line); kind: id | num | str | p


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    append = tokens.append
    i, n, line = 0, len(text), 1
    template_braces: list[int] = []  # brace depth at which each open ${ ... } resumes its template
    depth = 0

    def template(start: int, at_line: int) -> tuple[int, int, bool]:
        """Scan template text from start; True means it stopped inside a ${ expression."""
        j, here = start, at_line
        while j < n:
            ch = text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "\n":
                here += 1
            elif ch == "`":
                return j + 1, here, False
            elif ch == "$" and text[j + 1:j + 2] == "{":
                return j + 2, here, True
            j += 1
        return n, here, False

    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
        elif ch in " \t\r\f\v\ufeff":
            i += 1
        elif ch == "/" and text[i + 1:i + 2] == "/":
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif ch == "/" and text[i + 1:i + 2] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
        elif ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                if text[j] == "\\" and text[j + 1:j + 2] == "\n":
                    line += 1
                j += 2 if text[j] == "\\" else 1
            # An unterminated quote is usually JSX text ("don't"): recover at the line end.
            append(("str", "", line))
            i = j + 1 if j < n and text[j] == ch else j
        elif ch == "`":
            i, line, inside = template(i + 1, line)
            append(("str", "", line))
            if inside:
                depth += 1
                template_braces.append(depth)
        elif ch == "/":
            previous = tokens[-1] if tokens else None
            may_be_regex = (previous is None or (previous[0] == "p" and previous[1] not in (")", "]", "}"))
                            or (previous[0] == "id" and previous[1] in REGEX_AFTER_WORD))
            if may_be_regex:
                j, in_class, closed = i + 1, False, False
                while j < n and text[j] != "\n":
                    c = text[j]
                    if c == "\\":
                        j += 2
                        continue
                    if c == "[":
                        in_class = True
                    elif c == "]":
                        in_class = False
                    elif c == "/" and not in_class:
                        closed = True
                        break
                    j += 1
                if closed:
                    j += 1
                    while j < n and text[j].isalpha():
                        j += 1
                    append(("str", "", line))
                    i = j
                    continue
            append(("p", "/", line))
            i += 1
        elif ch in "0123456789":  # str.isdigit() is also true for characters like ²
            match = NUMBER.match(text, i)
            append(("num", "", line))
            i = match.end()
        else:
            match = IDENT.match(text, i)
            if match:
                append(("id", match.group(), line))
                i = match.end()
            elif text.startswith("=>", i):
                append(("p", "=>", line))
                i += 2
            elif ch == "{":
                depth += 1
                append(("p", ch, line))
                i += 1
            elif ch == "}":
                if template_braces and template_braces[-1] == depth:
                    template_braces.pop()
                    depth -= 1
                    i, line, inside = template(i + 1, line)
                    append(("str", "", line))
                    if inside:
                        depth += 1
                        template_braces.append(depth)
                else:
                    depth -= 1
                    append(("p", ch, line))
                    i += 1
            else:
                append(("p", ch, line))
                i += 1
    return tokens


def identifiers(text: str) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for kind, value, line in tokenize(text):
        if kind == "id" and value not in KEYWORDS:
            found.setdefault(value, []).append(line)
    return found


class _Parser:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.lines = text.split("\n")
        self.symbols: list[dict] = []
        self.count = len(self.tokens)
        self.angle_steps = 2_000_000  # a whole-file limit: a < rarely opens generics in minified code

    def value(self, i: int) -> str | None:
        return self.tokens[i][1] if 0 <= i < self.count and self.tokens[i][0] in ("id", "p") else None

    def is_name(self, i: int) -> bool:
        return 0 <= i < self.count and self.tokens[i][0] == "id"

    def balanced(self, i: int) -> int:
        """Return the index after the bracket closing the one at i."""
        stack: list[str] = []
        while i < self.count:
            kind, value, _ = self.tokens[i]
            if kind == "p":
                if value in OPENERS:
                    stack.append(OPENERS[value])
                elif stack and value == stack[-1]:
                    stack.pop()
                    if not stack:
                        return i + 1
            i += 1
        return self.count

    def angle(self, i: int) -> int:
        """Skip generic parameters starting at i, stopping safely if they do not close."""
        depth = 0
        limit = i + 200
        while i < self.count and i < limit and self.angle_steps > 0:
            self.angle_steps -= 1
            value = self.value(i)
            if value == "<":
                depth += 1
            elif value == ">":
                depth -= 1
                if depth <= 0:
                    return i + 1
            elif value == "=>":
                pass
            elif value in OPENERS:
                i = self.balanced(i)
                continue
            elif value in (";", ")", "}"):
                return i
            i += 1
        return i

    def statement_end(self, i: int, *, stop_at_comma: bool = False) -> int:
        """Index of the token ending a statement that continues from i (exclusive of a new one)."""
        while i < self.count:
            kind, value, line = self.tokens[i]
            if kind == "p" and value in OPENERS:
                i = self.balanced(i)
                continue
            if kind == "p" and value in (";", ")", "]", "}"):
                return i
            if kind == "p" and value == "," and stop_at_comma:
                return i
            if kind == "p" and value == "<" and stop_at_comma and self.is_name(i - 1):
                # new Map<string, number>(): those commas do not separate declarators.
                after = self.angle(i)
                if self.value(after - 1) == ">" and (self.value(after) in ("(", ";", ",", None)
                                                     or self.tokens[after - 1][2] < self.tokens[min(after, self.count - 1)][2]):
                    i = after
                    continue
            if i and self.tokens[i - 1][2] < line and self.value(i - 1) not in CONTINUES_LINE:
                # A new line starts a statement unless the old one visibly carries on.
                if kind == "p" and value == "@":
                    return i
                if kind == "id" and (value in STATEMENT_START or value not in KEYWORDS):
                    return i
            i += 1
        return self.count

    def is_callable(self, i: int) -> bool:
        if self.value(i) == "async":
            i += 1
        value = self.value(i)
        if value == "function":
            return True
        if self.is_name(i) and self.value(i + 1) == "=>":
            return True
        if value == "<":
            i = self.angle(i)
            value = self.value(i)
        if value == "(":
            after = self.balanced(i)
            if self.value(after) == "=>":
                return True
            if self.value(after) == ":":
                end = self.statement_end(after, stop_at_comma=True)
                return any(self.value(j) == "=>" for j in range(after, end))
        return False

    def add(self, name: str, kind: str, first: int, last: int) -> None:
        start = self.tokens[first][2]
        end = self.tokens[min(last, self.count - 1)][2]
        signature = self.lines[start - 1].strip()[:120] if start <= len(self.lines) else ""
        self.symbols.append({"name": name, "kind": kind, "line": start, "end": max(start, end),
                             "signature": signature})

    def decorator(self, i: int) -> int:
        i += 1
        while self.is_name(i):
            i += 1
            if self.value(i) == ".":
                i += 1
            else:
                break
        return self.balanced(i) if self.value(i) == "(" else i

    def callable_end(self, i: int) -> int:
        """From a parameter list's '(', return the index of the last token of the signature or body."""
        i = self.balanced(i) if self.value(i) == "(" else i
        if self.value(i) == ":":
            i += 1
            angle = 0
            while i < self.count:
                value = self.value(i)
                if value == "<":
                    angle += 1
                elif value == ">":
                    angle = max(0, angle - 1)
                elif value == "{" and not angle:
                    # An object return type is followed by the real body or more type syntax.
                    after = self.balanced(i)
                    if self.value(after) in ("{", "|", "&", "[", "<"):
                        i = after
                        continue
                    break
                elif value in OPENERS:
                    i = self.balanced(i)
                    continue
                elif value in (";", "}") and not angle:
                    break
                elif (not angle and self.tokens[i - 1][2] < self.tokens[i][2]
                      and self.value(i - 1) not in CONTINUES_LINE):
                    return i - 1
                i += 1
        if self.value(i) == "{":
            return self.balanced(i) - 1
        return i if self.value(i) == ";" else max(i - 1, 0)

    def scope(self, i: int, prefix: str = "") -> int:
        """Read declarations until the end of input or the brace closing this scope."""
        while i < self.count:
            start = i
            value = self.value(i)
            if value == "}":
                return i + 1
            if value == "@":
                i = self.decorator(i)
                continue
            if value in MODIFIERS and (self.is_name(i + 1) or self.value(i + 1) == "@"):
                while self.value(i) in MODIFIERS and self.is_name(i + 1):
                    i += 1
                if self.value(start) == "export" and self.value(start + 1) == "default" and self.value(i) not in (
                        "function", "class", "abstract", "async", "interface"):
                    i = self.statement_end(i) + 1
                    continue
                value = self.value(i)
            is_default = any(self.value(j) == "default" for j in range(start, i))

            if value == "function":
                j = i + 1
                if self.value(j) == "*":
                    j += 1
                name = self.tokens[j][1] if self.is_name(j) else ("default" if is_default else None)
                if self.is_name(j):
                    j += 1
                if self.value(j) == "<":
                    j = self.angle(j)
                if self.value(j) == "(" and name:
                    last = self.callable_end(j)
                    self.add(prefix + name, "function", start, last)
                    i = last + 1
                    continue
                i = j + 1 if j == i + 1 else j
                continue
            if value == "class":
                j = i + 1
                name = self.tokens[j][1] if self.is_name(j) and self.tokens[j][1] not in ("extends", "implements") else (
                    "default" if is_default else None)
                while j < self.count and self.value(j) != "{":
                    if self.value(j) == "(":
                        j = self.balanced(j)
                    elif self.value(j) == "<":
                        j = self.angle(j)
                    else:
                        j += 1
                if j >= self.count:
                    return self.count
                close = self.balanced(j)
                if name:
                    self.add(prefix + name, "class", start, close - 1)
                self.class_body(j + 1, close - 1, prefix + (name or "default") + ".")
                i = close
                continue
            if value in ("interface", "enum") and self.is_name(i + 1):
                j = i + 2
                while j < self.count and self.value(j) != "{":
                    if self.value(j) in (";",):
                        break
                    j = self.angle(j) if self.value(j) == "<" else j + 1
                if self.value(j) == "{":
                    close = self.balanced(j)
                    self.add(prefix + self.tokens[i + 1][1], value, start, close - 1)
                    i = close
                    continue
                i = j
                continue
            if value == "type" and self.is_name(i + 1) and self.value(i + 2) in ("=", "<"):
                j = i + 2
                if self.value(j) == "<":
                    j = self.angle(j)
                end = self.statement_end(j + 1)
                self.add(prefix + self.tokens[i + 1][1], "type", start, end if self.value(end) == ";" else end - 1)
                i = end + 1 if self.value(end) == ";" else end
                continue
            if value in ("namespace", "module") and (self.is_name(i + 1) or (
                    i + 1 < self.count and self.tokens[i + 1][0] == "str")):
                j = i + 1
                name = self.tokens[j][1] if self.is_name(j) else ""
                j += 1
                while self.value(j) == "." and self.is_name(j + 1):
                    name += "." + self.tokens[j + 1][1]
                    j += 2
                if self.value(j) == "{":
                    close = self.balanced(j)
                    if name:
                        self.add(prefix + name, "namespace", start, close - 1)
                    self.scope(j + 1, prefix + (name + "." if name else ""))
                    i = close
                    continue
                i = j
                continue
            if value == "const" and self.value(i + 1) == "enum":
                i += 1
                continue
            if value in ("const", "let", "var"):
                i = self.variables(i + 1, start, prefix)
                continue
            if value == "exports" or (value == "module" and self.value(i + 1) == "." and self.value(i + 2) == "exports"):
                j = i + (3 if value == "module" else 1)
                if self.value(j) == "." and self.is_name(j + 1) and self.value(j + 2) == "=":
                    end = self.statement_end(j + 3)
                    kind = "function" if self.is_callable(j + 3) else "const"
                    self.add(prefix + self.tokens[j + 1][1], kind, start, end if self.value(end) == ";" else end - 1)
                    i = end
                    continue
                i = j
                continue
            if value in OPENERS:
                i = self.balanced(i)
                continue
            i += 1
        return i

    def variables(self, i: int, start: int, prefix: str) -> int:
        """Read one declaration list; destructuring patterns name no single symbol."""
        while i < self.count:
            if self.value(i) in ("{", "["):
                i = self.balanced(i)
                name = None
            elif self.is_name(i):
                name = self.tokens[i][1]
                i += 1
            else:
                return i
            i = self.annotation(i)
            end = self.statement_end(i, stop_at_comma=True)
            if name:
                eq = i if self.value(i) == "=" else None
                kind = "function" if eq is not None and self.is_callable(eq + 1) else "const"
                self.add(prefix + name, kind, start, end if self.value(end) == ";" else end - 1)
            if self.value(end) != ",":
                return end + 1 if self.value(end) == ";" else end
            i = end + 1
            start = i
        return i

    def annotation(self, i: int) -> int:
        """Skip ': Type' up to '=' or the declaration's end; commas in generics are not separators."""
        if self.value(i) != ":":
            return i
        i += 1
        angle = 0
        while i < self.count:
            value = self.value(i)
            if value == "<":
                angle += 1
            elif value == ">":
                angle = max(0, angle - 1)
            elif value in OPENERS:
                i = self.balanced(i)
                continue
            elif not angle and value in ("=", ";", ",", ")", "}"):
                return i
            elif not angle and self.tokens[i - 1][2] < self.tokens[i][2] and self.value(i - 1) not in CONTINUES_LINE:
                return i
            i += 1
        return i

    def class_body(self, i: int, stop: int, prefix: str) -> None:
        while i < stop:
            first = i
            while i < stop:
                value = self.value(i)
                if value == "@":
                    i = self.decorator(i)
                elif value in MODIFIERS or value in ("get", "set"):
                    if self.value(i + 1) in ("(", "=", ":", ";", "?", "!", "<"):
                        break  # a member that is itself called get, static, ...
                    i += 1
                elif value == "*":
                    i += 1
                else:
                    break
            if i >= stop:
                return
            value = self.value(i)
            if value == ";":
                i += 1
                continue
            if value == "{":  # static initialization block
                i = self.balanced(i)
                continue
            if value == "[":
                i = self.balanced(i)
                name = None
            elif self.is_name(i):
                name = self.tokens[i][1]
                i += 1
            elif i < self.count and self.tokens[i][0] == "str":
                name = None
                i += 1
            else:
                i += 1
                continue
            if self.value(i) in ("?", "!"):
                i += 1
            if self.value(i) == "<":
                i = self.angle(i)
            value = self.value(i)
            if value == "(":
                last = self.callable_end(i)
                if name:
                    self.add(prefix + name, "method", first, last)
                i = last + 1
            elif value == "=" or value == ":":
                end = self.statement_end(i + 1)
                end = min(end, stop)
                if name and value == "=" and self.is_callable(i + 1):
                    self.add(prefix + name, "method", first, end if self.value(end) == ";" else end - 1)
                i = end + 1 if self.value(end) == ";" else end
                if i == first:
                    i += 1
            elif i == first:
                i += 1


def js_symbols(text: str) -> list[dict]:
    parser = _Parser(text)
    parser.scope(0)
    return parser.symbols
