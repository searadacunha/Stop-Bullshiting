import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import stop_bullshiting.banner as banner_module
from stop_bullshiting.cli import main
from stop_bullshiting.jsparse import identifiers, js_symbols
from stop_bullshiting.repomap import ranked_map
from stop_bullshiting.symbols import definitions, excerpt, file_symbols, references, symbol_map, symbol_source

def file_symbols_of(text):
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "input.ts"
        path.write_text(text)
        return file_symbols(path)


TS = r'''// function commented() {}
import x from "y";
export const A = 1, B = (a) => { return `t${a}`; };
export const M: Map<string, number> = new Map<string, number>(), N = 2;
export default function main<T>(a: T): { x: number } { const inner = 1; return { x: 1 }; }
export async function* gen() { yield 1 }
function over(a: string): void;
function over(a: any) { return /\/\}{/.test(a); }
async function shape(): Promise<{ a: string }> {
  return { a: "}" };
}
@Dec({ a: 1 })
export abstract class Foo<T> extends Bar(Baz) implements Q {
  private x = 1;
  handler = async (e) => { return e; };
  constructor(private a: string) { super(); }
  @Input() get value(): string { return "}"; }
  abstract run(): void;
  get() { return 1 }
}
export interface I<T> { a: T; b(): void }
export type U<T> = T | { a: 1 };
export enum E { A, B }
const enum CE { X }
namespace NS { export function fn() {} }
exports.cjs = function () {};
const Comp = () => <div>don't {x}</div>;
const { hiddenA, hiddenB } = obj;
const url = `http://${host}/${`nested ${y}`}`;
const noSemi = 42
function afterNoSemi() {}
const text = "function fake() {}";
'''


class JavaScriptTests(unittest.TestCase):
    def test_declarations_and_exact_ranges(self):
        found = {(s["name"], s["kind"]): (s["line"], s["end"]) for s in js_symbols(TS)}
        expected = {("A", "const"): (3, 3), ("B", "function"): (3, 3), ("M", "const"): (4, 4), ("N", "const"): (4, 4),
                    ("main", "function"): (5, 5), ("gen", "function"): (6, 6), ("over", "function"): (8, 8),
                    ("shape", "function"): (9, 11), ("Foo", "class"): (13, 20), ("Foo.handler", "method"): (15, 15),
                    ("Foo.constructor", "method"): (16, 16), ("Foo.value", "method"): (17, 17),
                    ("Foo.run", "method"): (18, 18), ("Foo.get", "method"): (19, 19), ("I", "interface"): (21, 21),
                    ("U", "type"): (22, 22), ("E", "enum"): (23, 23), ("CE", "enum"): (24, 24),
                    ("NS", "namespace"): (25, 25), ("NS.fn", "function"): (25, 25), ("cjs", "function"): (26, 26),
                    ("Comp", "function"): (27, 27), ("url", "const"): (29, 29), ("noSemi", "const"): (30, 30),
                    ("afterNoSemi", "function"): (31, 31), ("text", "const"): (32, 32)}
        for key, value in expected.items():
            self.assertEqual(found.get(key), value, key)
        names = {s["name"] for s in js_symbols(TS)}
        for absent in ("commented", "fake", "inner", "hiddenA", "x", "Foo.x", "string", "number"):
            self.assertNotIn(absent, names)

    def test_identifiers_skip_strings_comments_and_keep_template_expressions(self):
        found = identifiers('// nope\nconst a = "nope2" + `x${real}y`; /* nope3 */ call(a);\n')
        self.assertEqual(found["real"], [2])
        self.assertEqual(found["a"], [2, 2])
        for absent in ("nope", "nope2", "nope3", "const"):
            self.assertNotIn(absent, found)

    def test_hostile_source_stays_fast(self):
        started = time.perf_counter()
        for text in ("const x = " + "a<b," * 20000, "const a=(b):c," * 8000, "class A {" + "x<y;" * 20000,
                     "function f<" * 8000, "`${" * 8000, "namespace a {" * 400):
            js_symbols_safely = file_symbols_of(text)
            self.assertIsInstance(js_symbols_safely, list)
        self.assertLess(time.perf_counter() - started, 20)

    def test_non_ascii_digit_does_not_raise(self):
        self.assertEqual([s["name"] for s in js_symbols("const x = ²;\nfunction after() {}\n")], ["x", "after"])

    def test_malformed_source_does_not_raise(self):
        for broken in ("function (", "class {", "`${", "const", "/* open", "'open", "export default", "}}}}", "@", "<", "a = /x",
                       "class A { get", "type X<", "function f(): {"):
            js_symbols(broken)
            identifiers(broken)


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, {"SBS_HOME": str(self.root / ".state")})
        self.env.start()
        (self.root / "core.py").write_text('class Ledger:\n    def settle(self, amount):\n        return amount\n\n\n'
                                           'def helper():\n    """settle in a docstring"""\n    return 1\n')
        (self.root / "use_a.py").write_text("from core import Ledger\nLedger().settle(3)\n# settle in a comment\n")
        (self.root / "use_b.py").write_text("from core import Ledger, helper\nhelper()\nLedger().settle(4)\n")
        (self.root / "web.ts").write_text("export function settleInvoice(id: string) {\n  return id;  \n}\n")
        (self.root / "front.tsx").write_text('import { settleInvoice } from "./web";\nsettleInvoice("x");\n')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_map_lists_every_language(self):
        labels = "\n".join(symbol_map(self.root))
        self.assertIn("core.py:2-3 def Ledger.settle", labels)
        self.assertIn("web.ts:1-3 function settleInvoice", labels)

    def test_definition_by_short_or_dotted_name(self):
        self.assertEqual([d["path"] for d in definitions("settle", self.root)], ["core.py"])
        self.assertEqual([d["line"] for d in definitions("Ledger.settle", self.root)], [2])
        self.assertEqual(definitions("sett", self.root), [])

    def test_references_exclude_definitions_strings_and_comments(self):
        found = references("settle", self.root)
        self.assertEqual(sorted(line.split(":")[0] + ":" + line.split(":")[1] for line in found),
                         ["use_a.py:2", "use_b.py:3"])
        self.assertEqual(references("settleInvoice", self.root)[0].split(":")[:2], ["front.tsx", "1"])

    def test_symbol_source_is_exact(self):
        text = symbol_source("settleInvoice", self.root)
        self.assertIn("web.ts:1-3 function settleInvoice", text)
        self.assertIn("    2:   return id;  ", text)  # trailing spaces are part of the source
        self.assertEqual(symbol_source("nothing", self.root), "")

    def test_symbol_source_stays_within_its_budget(self):
        (self.root / "big.py").write_text("def huge():\n" + "    x = 1\n" * 4000)
        (self.root / "big2.py").write_text("def huge():\n" + "    y = 1\n" * 4000)
        text = symbol_source("huge", self.root, max_chars=70000)
        self.assertIn("1 more match(es) over budget", text)
        self.assertIn("sbs read big2.py 1 250", text)

    def test_ranked_map_prefers_depended_on_files_and_keeps_its_budget(self):
        text = ranked_map(self.root, budget=4000)
        self.assertEqual(text.split("\n")[0], "core.py")
        self.assertIn("class Ledger:", text)
        small = ranked_map(self.root, budget=140)
        self.assertLessEqual(len(small), 140)
        self.assertEqual(ranked_map(self.root / "core.py", budget=500).split("\n")[0], "core.py")
        focused = ranked_map(self.root, budget=4000, focus=("settleInvoice",))
        self.assertEqual(focused.split("\n")[0], "web.ts")

    def test_ranked_map_of_an_empty_tree(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(ranked_map(empty), "")

    def test_cli_commands(self):
        def run(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(list(args))
            return code, out.getvalue()
        self.assertIn("core.py:2-3", run("def", "settle", str(self.root))[1])
        self.assertEqual(run("def", "nothing", str(self.root))[0], 1)
        self.assertIn("name matches", run("refs", "settle", str(self.root))[1])
        self.assertEqual(run("refs", "nothing", str(self.root))[0], 1)
        self.assertIn("return amount", run("symbol", "settle", str(self.root))[1])
        self.assertEqual(run("symbol", "nothing", str(self.root))[0], 1)
        self.assertIn("core.py", run("map", str(self.root), "--ranked", "--budget", "500")[1])

    def test_excerpt_keeps_trailing_whitespace(self):
        self.assertEqual(excerpt(self.root / "web.ts", 2, 2), "    2:   return id;  ")

    def test_excerpt_keeps_crlf_line_endings(self):
        (self.root / "dos.py").write_bytes(b"def dos():\r\n    return 1\r\n")
        self.assertEqual(excerpt(self.root / "dos.py", 1, 2), "    1: def dos():\r\n    2:     return 1\r")

    def test_symlink_out_of_the_tree_is_not_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "secret.py").write_text("def leaked_symbol():\n    pass\n")
        os.symlink(Path(outside.name) / "secret.py", self.root / "link.py")
        self.assertEqual(definitions("leaked_symbol", self.root), [])


class IdentityTests(unittest.TestCase):
    def test_banner_file_is_byte_identical(self):
        data = Path(banner_module.__file__).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "a3689eb3a9fca8a14f9117a505b72550d7edc5099da76694c17b0e1776ff3db1")


class CommandSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "SBS_HOME": self.temp.name + "/s", "PYTHONPATH": str(Path("src").resolve())}

    def tearDown(self):
        self.temp.cleanup()

    def _sbs(self, *args, **kwargs):
        return subprocess.run([sys.executable, "-m", "stop_bullshiting", *args], capture_output=True,
                              env=kwargs.pop("env", self.env), **kwargs)

    def test_failing_command_keeps_output_and_code_with_a_filter_available(self):
        code = "import sys; print('pkg/a.py:1:x\\n' * 2000); sys.exit(3)"
        proc = self._sbs("run", "--", sys.executable, "-c", code)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout.count(b"pkg/a.py:1:x"), 2000)

    def test_shell_wrapper_runs_the_command_when_no_temp_dir_exists(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "f.txt").write_text("needle\n")
            with patch.dict(os.environ, self.env):
                script = ("import tempfile, sys\n"
                          "from unittest.mock import patch\n"
                          "from stop_bullshiting.cli import main\n"
                          "with patch.object(tempfile, 'TemporaryFile', side_effect=FileNotFoundError('no tmp')):\n"
                          "    sys.exit(main(['shell', 'grep -n needle f.txt']))\n")
                proc = subprocess.run([sys.executable, "-c", script], capture_output=True, cwd=raw, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"1:needle\n")

    def test_compress_stdin_keeps_input_when_storage_fails(self):
        state = Path(self.temp.name) / "s"
        state.mkdir()
        (state / "store.sqlite3").write_text("not a database" * 100)
        text = ("line of log\n" * 2000).encode()
        proc = self._sbs("compress", "--mode", "plain", input=text)
        self.assertEqual(proc.stdout, text)
        self.assertIn(b"preserving original input", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_compress_stdin_keeps_non_utf8_bytes_exactly(self):
        text = b"caf\xe9 " * 2000
        self.assertEqual(self._sbs("compress", input=text).stdout, text)

    def test_oversized_stdin_passes_through_without_being_buffered(self):
        text = b"0123456789abcdef" * (1024 * 1024 + 8)
        proc = self._sbs("compress", input=text)
        self.assertEqual(len(proc.stdout), len(text))
        self.assertEqual(proc.stdout[:16], text[:16])
        self.assertIn(b"bypassing reduction", proc.stderr)

    def test_hook_always_exits_zero(self):
        for raw in (b"", b"\xff\xfe", b"{", b"[]"):
            for agent in ("claude", "codex"):
                proc = self._sbs("hook", agent, input=raw)
                self.assertEqual((proc.returncode, proc.stdout), (0, b""), raw)
        state = Path(self.temp.name) / "s"
        state.mkdir()
        (state / "store.sqlite3").write_text("corrupt" * 100)
        event = ('{"hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"rg x"},'
                 '"tool_response":{"stdout":"' + "a.py:1:x\\n" * 900 + '"}}').encode()
        proc = self._sbs("hook", "claude", input=event)
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))

    def test_doctor_reports_wiring_without_claiming_activity(self):
        env = {**self.env, "HOME": self.temp.name}
        out = self._sbs("doctor", env=env).stdout.decode()
        self.assertIn("SBS hook: not installed", out)
        self.assertIn("FTS5", out)
        self.assertIn("not proof of reduction", out)


if __name__ == "__main__":
    unittest.main()
