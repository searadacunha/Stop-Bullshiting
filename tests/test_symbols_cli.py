import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.cli import main
from stop_bullshiting.symbols import excerpt, python_symbols, search, symbol_map


class SymbolsAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"SBS_HOME": str(self.path / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_ast_map(self):
        file = self.path / "example.py"
        file.write_text("class Demo:\n    def execute(self):\n        pass\n\ndef top():\n    return 3\n")
        syms = python_symbols(file)
        self.assertEqual([s["name"] for s in syms], ["Demo", "Demo.execute", "top"])
        self.assertEqual(len(symbol_map(self.path, name_filter="execute")), 1)
        self.assertIn("2:", excerpt(file, 2, 3))
        self.assertTrue(search("return 3", self.path))

    def test_cli_banner_and_bench(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["banner"]), 0)
        self.assertIn("B U L L S H I T I N G", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as f:
            self.assertEqual(main(["bench"]), 0)
        self.assertIn("model_tokens_measured", f.getvalue())

    def test_command_exit_code_passthrough(self):
        cmd = [sys.executable, "-c", "import sys;sys.exit(7)"]
        self.assertEqual(main(["run", "--"] + cmd), 7)


if __name__ == "__main__":
    unittest.main()
