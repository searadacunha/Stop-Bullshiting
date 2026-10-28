import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stop_bullshiting.mcp import dispatch, invoke
from stop_bullshiting.store import save_receipt


class McpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_init_and_tools(self):
        reply = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "stop-bullshiting")
        tools = dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertGreaterEqual(len(tools), 7)
        self.assertEqual(dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}), None)

    def test_tool_roundtrip(self):
        large = "DEBUG line one\n" * 400
        compact = invoke("sbs_compact", {"text": large})["content"][0]["text"]
        self.assertIn("sbs show", compact)
        ident = compact.split("sbs show ")[1].split("]")[0]
        original = invoke("sbs_restore", {"receipt": ident})["content"][0]["text"]
        self.assertEqual(original, large)
        self.assertTrue(invoke("nonexistent", {})["isError"])

    def test_restore_schema_exposes_optional_targeted_retrieval(self):
        tools = dispatch({"id": 3, "method": "tools/list"})["result"]["tools"]
        schema = next(tool for tool in tools if tool["name"] == "sbs_restore")["inputSchema"]
        self.assertEqual(schema["required"], ["receipt"])
        self.assertTrue({"start", "end", "query", "context", "limit"} <= set(schema["properties"]))

    def test_restore_can_reach_late_diagnostics_without_resending_original(self):
        original = "ordinary output\n" * 2000 + "UNUSUAL_STATE Ω-request-123456789\nlast\n"
        receipt = save_receipt(original, source="test", mode="plain", visible_chars=100)
        reply = invoke("sbs_restore", {"receipt": receipt, "query": "UNUSUAL_STATE", "context": 0})
        self.assertFalse(reply["isError"])
        view = reply["content"][0]["text"]
        self.assertIn("2001: UNUSUAL_STATE Ω-request-123456789\n", view)
        self.assertNotIn("ordinary output", view)
        self.assertLess(len(view), 1000)
        window = invoke("sbs_restore", {"receipt": receipt, "start": 2001, "end": 2002})
        self.assertIn("2002: last\n", window["content"][0]["text"])

    def test_large_default_restore_is_capped_and_explains_targeted_access(self):
        original = "x" * 25000 + "\nshort tail\n"
        receipt = save_receipt(original, source="test", mode="plain", visible_chars=100)
        reply = invoke("sbs_restore", {"receipt": receipt})
        view = reply["content"][0]["text"]
        self.assertFalse(reply["isError"])
        self.assertLessEqual(len(view), 20000)
        self.assertIn("SBS line 1 omitted:", view)
        self.assertIn("2: short tail\n", view)
        self.assertIn("start/end/query", view)

    def test_targeted_restore_reports_invalid_arguments_as_tool_errors(self):
        receipt = save_receipt("first\nsecond\n", source="test", mode="plain", visible_chars=1)
        for selection in ({"start": 0}, {"end": False}, {"query": ""}, {"limit": -1}, {"context": "2"}):
            with self.subTest(selection=selection):
                reply = invoke("sbs_restore", {"receipt": receipt, **selection})
                self.assertTrue(reply["isError"])
                self.assertIn("ValueError", reply["content"][0]["text"])

    def test_database_errors_remain_mcp_tool_errors(self):
        import sqlite3
        with patch("stop_bullshiting.mcp.get_receipt", side_effect=sqlite3.DatabaseError("invalid store")):
            reply = invoke("sbs_restore", {"receipt": "unknown"})
        self.assertTrue(reply["isError"])
        self.assertIn("DatabaseError", reply["content"][0]["text"])


    def test_mcp_files_and_notes_are_confined_to_workspace(self):
        root = Path(self.temp.name)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "allowed.py").write_text("def allowed():\n    return 'visible'\n")
        private = root / "private.py"
        private.write_text("def secret():\n    return 'DO_NOT_READ'\n")
        (workspace / "escape.py").symlink_to(private)
        with patch.dict(os.environ, {"SBS_WORKSPACE": str(workspace)}):
            ok = invoke("sbs_read", {"file": str(workspace / "allowed.py"), "start": 1, "end": 2})
            self.assertFalse(ok["isError"])
            self.assertIn("def allowed()", ok["content"][0]["text"])
            for forbidden in ("../private.py", str(private), "escape.py"):
                with self.subTest(forbidden=forbidden):
                    for name, args in (
                        ("sbs_read", {"file": forbidden, "start": 1, "end": 1}),
                        ("sbs_search", {"query": "DO_NOT_READ", "path": forbidden}),
                        ("sbs_symbols", {"path": forbidden}),
                        ("sbs_note_find", {"scope": forbidden}),
                        ("sbs_note_save", {"key": "x", "text": "x", "scope": forbidden}),
                    ):
                        reply = invoke(name, args)
                        self.assertTrue(reply["isError"], (name, forbidden))
                        self.assertIn("outside MCP workspace", reply["content"][0]["text"])
            # A directory search must also ignore symlinked files pointing out.
            listing = invoke("sbs_symbols", {"path": "."})
            self.assertFalse(listing["isError"])
            self.assertIn("allowed", listing["content"][0]["text"])
            self.assertNotIn("secret", listing["content"][0]["text"])
            hit = invoke("sbs_search", {"query": "DO_NOT_READ", "path": "."})
            self.assertFalse(hit["isError"])
            self.assertNotIn("DO_NOT_READ", hit["content"][0]["text"])

if __name__ == "__main__":
    unittest.main()
