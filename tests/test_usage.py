import json
import tempfile
import unittest
from pathlib import Path
from stop_bullshiting.usage import claude_usage, codex_usage


class UsageTests(unittest.TestCase):
    def test_claude_deduplicate_repeated_message(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "session.jsonl"
            records = [
                {"type": "assistant", "message": {"id": "msg_1", "usage": {"input_tokens": 100,
                 "output_tokens": 7, "cache_read_input_tokens": 30}}},
                {"type": "assistant", "message": {"id": "msg_1", "usage": {"input_tokens": 100,
                 "output_tokens": 7, "cache_read_input_tokens": 30}}},
                {"type": "assistant", "message": {"id": "msg_2", "usage": {"input_tokens": 50,
                 "output_tokens": 10}}},
            ]
            path.write_text("\n".join(map(json.dumps, records)))
            result = claude_usage(Path(d))
            self.assertEqual(result["messages"], 2)
            self.assertEqual(result["totals"]["input"], 150)
            self.assertEqual(result["totals"]["output"], 17)

    def test_codex_rolling_totals_not_summed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "session.jsonl"
            records = [{"payload": {"type": "token_count", "info": {"total_token_usage":
                {"input_tokens": t, "output_tokens": t // 5, "cached_input_tokens": t // 2}}}}
                for t in (100, 200, 400)]
            path.write_text("\n".join(map(json.dumps, records)))
            result = codex_usage(Path(d))
            self.assertEqual(result["totals"]["input"], 400)
            self.assertEqual(result["totals"]["output"], 80)
            self.assertEqual(result["totals"]["cache_read"], 200)


if __name__ == "__main__":
    unittest.main()
