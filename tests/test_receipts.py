import re
import unittest

from stop_bullshiting.receipts import select_receipt


def source_numbers(view):
    return [int(number) for number in re.findall(r"(?m)^(\d+): ", view)]


class ReceiptSelectionTests(unittest.TestCase):
    def test_numbered_range_keeps_exact_unicode_and_whitespace(self):
        original = "first\n  café λ\t  \r\n\tNAO_ENCONTRADO ☃  \nlast"
        view = select_receipt(original, start=2, end=3)
        self.assertIn("2:   café λ\t  \r\n", view)
        self.assertIn("3: \tNAO_ENCONTRADO ☃  \n", view)
        self.assertEqual(source_numbers(view), [2, 3])
        self.assertIn("total_lines=4", view)
        self.assertIn("selected_lines=2", view)
        self.assertIn("shown_lines=2", view)
        self.assertIn("omitted_lines=2", view)

    def test_query_finds_nonstandard_diagnostic_far_beyond_first_20000_chars(self):
        lines = [f"record {i}: normal status\n" for i in range(1500)]
        lines[1200] = "dispatch lost: request Ω-123456789-UNUSUAL_STATE\n"
        view = select_receipt("".join(lines), query="Ω-123456789-UNUSUAL_STATE", context=1)
        self.assertEqual(source_numbers(view), [1200, 1201, 1202])
        self.assertIn("1201: dispatch lost: request Ω-123456789-UNUSUAL_STATE\n", view)
        self.assertIn("match_lines=1", view)

    def test_literal_query_is_not_regex_and_remains_case_sensitive(self):
        view = select_receipt("HEAD.*\nhead.*\nHEAD anything\n", query="HEAD.*", context=0)
        self.assertEqual(source_numbers(view), [1])
        self.assertIn("match_lines=1", view)

    def test_one_line_limit_returns_the_match_instead_of_its_context(self):
        view = select_receipt("before\nneedle\nafter\n", query="needle", context=1, limit=1)
        self.assertEqual(source_numbers(view), [2])
        self.assertIn("2: needle\n", view)
        self.assertIn("selected_lines=3", view)
        self.assertIn("shown_lines=1", view)

    def test_large_context_cannot_spend_the_default_limit_before_the_match(self):
        lines = [f"ordinary line {i}\n" for i in range(210)]
        lines[100] = "MATCH-ONLY-HERE\n"
        view = select_receipt("".join(lines), query="MATCH-ONLY-HERE", context=100)
        numbers = source_numbers(view)
        self.assertEqual(len(numbers), 50)
        self.assertEqual(numbers, sorted(set(numbers)))
        self.assertIn(101, numbers)
        self.assertIn("101: MATCH-ONLY-HERE\n", view)
        for number in numbers:
            self.assertIn(f"{number}: {lines[number - 1]}", view)

    def test_multiple_matches_take_priority_over_context_in_source_order(self):
        view = select_receipt("before\nneedle A\nbetween\nneedle B\nafter\n",
                              query="needle", context=2, limit=2)
        self.assertEqual(source_numbers(view), [2, 4])
        self.assertIn("match_lines=2", view)

    def test_overlapping_context_windows_are_merged_once(self):
        view = select_receipt("one\nneedle A\nthree\nneedle B\nfive\nsix\n", query="needle", context=1)
        self.assertEqual(source_numbers(view), [1, 2, 3, 4, 5])
        self.assertIn("selected_lines=5", view)
        self.assertIn("match_lines=2", view)

    def test_query_and_context_are_clipped_to_requested_range(self):
        view = select_receipt("needle\ntwo\nneedle\nfour\nneedle\n", start=2, end=4,
                              query="needle", context=99)
        self.assertEqual(source_numbers(view), [2, 3, 4])
        self.assertIn("match_lines=1", view)

    def test_open_ranges_and_line_limit_report_omissions(self):
        view = select_receipt("one\ntwo\nthree\nfour\nfive", start=3, limit=2)
        self.assertEqual(source_numbers(view), [3, 4])
        self.assertIn("selected_lines=3", view)
        self.assertIn("shown_lines=2", view)
        self.assertIn("omitted_lines=3", view)
        self.assertIn("narrow start/end/query", view)
        self.assertEqual(source_numbers(select_receipt("one\ntwo\nthree", end=2)), [1, 2])

    def test_empty_missing_and_out_of_range_selections_are_explicit(self):
        for original, options, total in [("", {}, 0), ("one\ntwo", {"query": "missing"}, 2),
                                         ("one\ntwo", {"start": 9}, 2)]:
            with self.subTest(options=options):
                view = select_receipt(original, **options)
                self.assertIn(f"total_lines={total}", view)
                self.assertIn("selected_lines=0", view)
                self.assertIn("shown_lines=0", view)
                self.assertEqual(source_numbers(view), [])

    def test_single_long_line_is_explicitly_omitted_never_silently_shortened(self):
        original = "identifier=" + "x" * 1000 + "-EXACT-END\nkeep me\n"
        for budget in (256, 350, 1000):
            with self.subTest(budget=budget):
                view = select_receipt(original, max_chars=budget)
                self.assertLessEqual(len(view), budget)
                self.assertIn("SBS line 1 omitted:", view)
                self.assertNotIn("1: identifier=", view)
                self.assertNotIn("-EXACT-END", view)
                # A short diagnostic after an oversized line remains available.
                if budget >= 350:
                    self.assertIn("2: keep me\n", view)

    def test_character_budget_includes_markers_and_never_cuts_identifiers(self):
        original = "".join(f"id-{i:03d}-café-λ-COMPLETE\n" for i in range(100))
        view = select_receipt(original, max_chars=350, limit=100)
        self.assertLessEqual(len(view), 350)
        numbers = source_numbers(view)
        self.assertGreater(len(numbers), 0)
        self.assertLess(len(numbers), 100)
        for number in numbers:
            self.assertIn(f"{number}: id-{number - 1:03d}-café-λ-COMPLETE\n", view)
        self.assertIn(f"shown_lines={len(numbers)}", view)

    def test_omission_explains_metadata_when_source_alone_is_below_budget(self):
        original = "x" * 300 + "\n"
        view = select_receipt(original, max_chars=350)
        self.assertLess(len(original), 350)
        self.assertLessEqual(len(view), 350)
        self.assertIn("301 chars plus numbering/metadata exceed budget=350", view)
        self.assertIn("shown_lines=0", view)
        self.assertNotIn("1: x", view)

    def test_oversized_line_consumes_limit_without_hiding_that_fact(self):
        view = select_receipt("x" * 1000 + "\nsecond\n", limit=1, max_chars=350)
        self.assertIn("SBS line 1 omitted:", view)
        self.assertNotIn("2: second", view)
        self.assertIn("shown_lines=0", view)

    def test_bounds_and_types_are_validated_without_boolean_integer_coercion(self):
        invalid = [{"start": 0}, {"start": True}, {"start": 1.0}, {"end": 0},
                   {"start": 3, "end": 2}, {"end": "3"}, {"context": -1},
                   {"context": True}, {"limit": 0}, {"limit": 1.5},
                   {"max_chars": 255}, {"max_chars": True}, {"query": ""},
                   {"query": 4}, {"query": "two\nlines"}, {"query": "x\u2028y"}]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                select_receipt("one\ntwo\nthree\n", **options)
        with self.assertRaises(ValueError):
            select_receipt(b"not decoded")


if __name__ == "__main__":
    unittest.main()
