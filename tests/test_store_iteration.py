import os
import random
import tempfile
import tracemalloc
import unittest
import zlib
from unittest.mock import patch

from stop_bullshiting.store import db, iter_receipts, save_receipt


class StoreIterationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"SBS_HOME": self.temp.name})
        env.start()
        self.addCleanup(env.stop)

    def save(self, text, source="fixture"):
        return save_receipt(text, source=source, mode="plain", visible_chars=1)

    def test_unbounded_iteration_preserves_order_and_exact_text(self):
        samples = ["first\n", "  café\t\r\n日本語\n", "\ud800 lone surrogate", ""]
        with patch("stop_bullshiting.store.time.time", return_value=1700000000):
            identifiers = [self.save(text, source=f"fixture:{i}") for i, text in enumerate(samples)]
        rows = list(iter_receipts(3))
        self.assertEqual([row[0] for row in rows], identifiers[::-1][:3])
        self.assertEqual([row[3] for row in rows], samples[::-1][:3])
        self.assertEqual([row[2] for row in rows], ["fixture:3", "fixture:2", "fixture:1"])
        self.assertTrue(all(row[1] == 1700000000 for row in rows))
        self.assertEqual(list(iter_receipts(0)), [])

    def test_cumulative_budget_counts_utf8_bytes_and_continues_after_skips(self):
        oldest = self.save("ok")
        unicode_id = self.save("éé")  # Two characters, four UTF-8 bytes.
        newest = self.save("A" * 1000)
        rows = list(iter_receipts(3, max_bytes=3))
        self.assertEqual([(row[0], row[3]) for row in rows],
                         [(newest, None), (unicode_id, None), (oldest, "ok")])

    def test_accepted_receipts_share_one_budget(self):
        oldest = self.save("a")
        middle = self.save("é")
        newest = self.save("b")
        rows = list(iter_receipts(3, max_bytes=3))
        self.assertEqual([(row[0], row[3]) for row in rows],
                         [(newest, "b"), (middle, "é"), (oldest, None)])

    def test_zero_budget_and_surrogates_use_storage_encoding(self):
        empty = self.save("")
        surrogate = self.save("\ud800")
        self.assertEqual([(r[0], r[3]) for r in iter_receipts(2, max_bytes=0)],
                         [(surrogate, None), (empty, "")])
        self.assertEqual(next(iter_receipts(1, max_bytes=2))[3], None)
        self.assertEqual(next(iter_receipts(1, max_bytes=3))[3], "\ud800")

    def test_first_result_does_not_load_all_compressed_originals(self):
        # Incompressible synthetic input makes the old fetchall(BLOB) path
        # measurable, independent of how cheaply repetitive text compresses.
        sample = random.Random(17).randbytes(128 * 1024).hex()
        for i in range(16):
            self.save(sample, source=f"fixture:{i}")
        with db() as conn:
            total = conn.execute("SELECT SUM(LENGTH(original)) FROM receipts").fetchone()[0]
        originals = iter_receipts(16)
        tracemalloc.start()
        try:
            first = next(originals)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            originals.close()
            tracemalloc.stop()
        self.assertTrue(first[3] == sample, "first original changed during iteration")
        self.assertLess(peak, total // 2,
                        f"first result allocated {peak} bytes for {total} bytes of stored BLOBs")

    def test_oversized_original_is_not_fully_decompressed(self):
        # Multi-byte text passes the cheap character-count check but exceeds
        # the byte budget. The decompressor must stop after budget + one byte.
        sample = chr(0x1F600) * (512 * 1024)
        self.save(sample)
        budget = len(sample)
        tracemalloc.start()
        try:
            result = next(iter_receipts(1, max_bytes=budget))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(result[3] is None, "oversized original was decoded in full")
        self.assertLess(peak, budget * 3)

    def test_no_read_lock_is_held_across_yields_and_selection_is_stable(self):
        older = self.save("older")
        newer = self.save("newer")
        originals = iter_receipts(2)
        self.addCleanup(originals.close)
        self.assertEqual(next(originals)[0], newer)
        self.save("written while iterator is suspended")
        self.assertEqual([row[0] for row in originals], [older])

    def test_corrupt_accepted_original_is_not_reported_as_budget_skip(self):
        identifier = self.save("ok")
        for blob, error in [(b"invalid compressed data", zlib.error),
                            (zlib.compress(b"ok")[:-1], zlib.error),
                            (zlib.compress(b"\xff"), UnicodeDecodeError)]:
            with self.subTest(blob=blob):
                with db() as conn:
                    conn.execute("UPDATE receipts SET original=? WHERE id=?", (blob, identifier))
                with self.assertRaises(error):
                    list(iter_receipts(1, max_bytes=100))

    def test_invalid_bounds_are_rejected(self):
        for limit in (-1, True, 1.5, "1"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                list(iter_receipts(limit))
        for budget in (-1, True, 1.5, "1"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                list(iter_receipts(1, max_bytes=budget))


if __name__ == "__main__":
    unittest.main()
