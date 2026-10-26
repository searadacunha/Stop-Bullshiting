import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stop_bullshiting.mcp import dispatch, invoke


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


if __name__ == "__main__":
    unittest.main()
