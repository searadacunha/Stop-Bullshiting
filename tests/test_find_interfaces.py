import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stop_bullshiting.cli import main
from stop_bullshiting.mcp import dispatch, invoke
from stop_bullshiting.store import save_receipt


class FindInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.receipt = save_receipt("x" * 2000 + "\nE_TARGET_77 complete evidence\nlast\n",
                                    source="interface-test", mode="logs", visible_chars=1)

    def test_cli_budget_keeps_the_actual_match(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["find", "E_TARGET_77", "--max-chars", "512"])
        self.assertEqual(code, 0)
        self.assertLessEqual(len(output.getvalue()), 512)
        self.assertIn("2: E_TARGET_77 complete evidence", output.getvalue())

    def test_cli_json_exposes_coverage_with_the_same_matching_lines(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["find", "E_TARGET_77", "--max-chars", "512", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["max_chars"], 512)
        self.assertEqual(result["coverage"]["searched_receipts"], 1)
        self.assertEqual(result["hits"][0]["receipt"], self.receipt)
        self.assertIn("2: E_TARGET_77 complete evidence", result["hits"][0]["text"])

    def test_mcp_budget_keeps_the_actual_match(self):
        reply = invoke("sbs_find", {"query": "E_TARGET_77", "max_chars": 512})
        self.assertFalse(reply["isError"])
        text = reply["content"][0]["text"]
        self.assertLessEqual(len(text), 512)
        self.assertIn("2: E_TARGET_77 complete evidence", text)

    def test_invalid_budget_is_reported_by_both_interfaces(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["find", "E_TARGET_77", "--max-chars", "255"]), 2)
        for value in (255, 20001, True, "512"):
            with self.subTest(value=value):
                reply = invoke("sbs_find", {"query": "E_TARGET_77", "max_chars": value})
                self.assertTrue(reply["isError"])
                self.assertIn("max_chars", reply["content"][0]["text"])

    def test_mcp_advertises_optional_budget_without_changing_required_arguments(self):
        tools = dispatch({"id": 1, "method": "tools/list"})["result"]["tools"]
        schema = next(tool["inputSchema"] for tool in tools if tool["name"] == "sbs_find")
        self.assertEqual(schema["required"], ["query"])
        self.assertEqual(schema["properties"]["max_chars"]["default"], 6000)


if __name__ == "__main__":
    unittest.main()
