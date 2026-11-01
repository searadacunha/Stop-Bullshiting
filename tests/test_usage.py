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

    def test_claude_response_repeated_across_logs_is_counted_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}
            partial = {"input_tokens": 10, "output_tokens": 1}
            first = {"type": "assistant", "requestId": "req_1", "message": {"id": "msg_1", "usage": partial}}
            last = {"type": "assistant", "requestId": "req_1", "message": {"id": "msg_1", "usage": usage}}
            (root / "a.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(last) + "\n")
            (root / "sub").mkdir()
            (root / "sub" / "resumed.jsonl").write_text(json.dumps({**last, "isSidechain": True}) + "\n")
            result = claude_usage(root)
            self.assertEqual(result["logs_scanned"], 2)
            self.assertEqual(result["messages"], 1)
            self.assertEqual(result["totals"], {"input": 10, "output": 5, "cache_read": 100, "cache_write": 0})

    def test_complete_record_beats_a_partial_main_thread_one(self):
        with tempfile.TemporaryDirectory() as d:
            partial = {"type": "assistant", "requestId": "r", "message": {"id": "m", "usage": {"output_tokens": 1}}}
            full = {"type": "assistant", "requestId": "r", "isSidechain": True,
                    "message": {"id": "m", "usage": {"output_tokens": 500}}}
            (Path(d) / "s.jsonl").write_text(json.dumps(partial) + "\n" + json.dumps(full) + "\n")
            self.assertEqual(claude_usage(Path(d))["totals"]["output"], 500)

    def test_large_logs_are_counted_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "big.jsonl"
            record = {"type": "assistant", "message": {"id": "m", "usage": {"input_tokens": 7, "output_tokens": 1}}}
            with path.open("w") as stream:
                stream.write(("x" * 1023 + "\n") * 45000)
                stream.write(json.dumps(record) + "\n")
            self.assertGreater(path.stat().st_size, 40 * 1024 * 1024)
            self.assertEqual(claude_usage(Path(d))["totals"]["input"], 7)

    def test_since_limits_both_agents(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.jsonl"
            old = {"timestamp": "2026-01-01T00:00:00Z", "type": "assistant",
                   "message": {"id": "a", "usage": {"input_tokens": 100, "output_tokens": 1}}}
            new = {"timestamp": "2026-03-01T00:00:00Z", "type": "assistant",
                   "message": {"id": "b", "usage": {"input_tokens": 3, "output_tokens": 1}}}
            path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
            self.assertEqual(claude_usage(Path(d), since="2026-02-01")["totals"]["input"], 3)
            rows = [{"timestamp": stamp, "payload": {"type": "token_count", "info": {"total_token_usage":
                     {"input_tokens": total, "output_tokens": 0}}}}
                    for stamp, total in (("2026-01-01T00:00:00Z", 100), ("2026-03-01T00:00:00Z", 130))]
            path.write_text("\n".join(map(json.dumps, rows)))
            self.assertEqual(codex_usage(Path(d), since="2026-02-01")["totals"]["input"], 30)


if __name__ == "__main__":
    unittest.main()
