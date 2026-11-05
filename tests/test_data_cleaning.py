"""Cleaning contracts use synthetic observations only."""
import unittest
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.data.clean_workbooks import clean_frame, export_workbook, is_blank, verify_export


class CleaningTests(unittest.TestCase):
    def test_preserve_all_policy_keeps_empty_columns_while_removing_duplicate_rows(self):
        frame = pd.DataFrame({"code": ["a", "a", "b"], "empty": [None, None, None],
                              "partial": [None, None, "x"]}, dtype=object)
        result, stats, _ = clean_frame(frame, drop_empty_columns=False)
        self.assertEqual(result.columns.tolist(), frame.columns.tolist())
        self.assertEqual(len(result), 2)
        self.assertEqual(stats["duplicate_rows_removed"], 1)
        self.assertEqual(stats["empty_columns_removed"], [])
        self.assertEqual(stats["entirely_blank_columns_detected"], ["empty"])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "preserved.xlsx"
            export_workbook(target, {"data": result}, {"data": {}})
            verified = verify_export(target, {"data": result}, allow_empty_columns=True)
            self.assertEqual(verified["remaining_entirely_blank_columns"], 1)

    def test_only_identical_full_rows_removed_and_first_order_preserved(self):
        frame = pd.DataFrame([
            ["a", 2, None], ["a", 2, None], ["a", 3, None], ["A", 2, None], ["a ", 2, None]
        ], columns=["code", "quantity", "blank"], dtype=object)
        original = frame.copy(deep=True)
        result, stats, lineage = clean_frame(frame)
        self.assertEqual(result.values.tolist(), [["a", 2], ["a", 3], ["A", 2], ["a ", 2]])
        self.assertEqual(stats["duplicate_rows_removed"], 1)
        self.assertEqual(lineage, [{"removed_excel_row": 3, "retained_excel_row": 2}])
        pd.testing.assert_frame_equal(frame, original)

    def test_drop_only_entirely_blank_columns_and_preserve_zero_and_na_labels(self):
        frame = pd.DataFrame({
            "empty": [None, "", "  "], "partial": [None, "x", None],
            "zero": [0, 0, 0], "labels": ["NA", "N/A", "NULL"]
        }, dtype=object)
        result, stats, _ = clean_frame(frame)
        self.assertEqual(stats["empty_columns_removed"], ["empty"])
        self.assertEqual(result.columns.tolist(), ["partial", "zero", "labels"])
        self.assertEqual(result["labels"].tolist(), ["NA", "N/A", "NULL"])

    def test_text_and_boolean_are_distinct_from_numeric_codes(self):
        result, stats, _ = clean_frame(pd.DataFrame({"code": [1, "1", True, 1]}, dtype=object))
        self.assertEqual(len(result), 3)
        self.assertEqual(stats["duplicate_rows_removed"], 1)

    def test_cleaning_is_idempotent(self):
        source = pd.DataFrame({"code": ["a", "a", "b"], "blank": [None, None, None]})
        first, _, _ = clean_frame(source)
        second, stats, _ = clean_frame(first)
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(stats["duplicate_rows_removed"], 0)
        self.assertEqual(stats["empty_columns_removed"], [])
        self.assertFalse(is_blank(0))
        self.assertFalse(is_blank(False))

    def test_export_round_trip_preserves_dates_codes_zero_and_literal_formula_text(self):
        frame = pd.DataFrame({
            "code": ["001", "NA"], "date": [datetime(2025, 1, 2), None],
            "quantity": [0, 1.25], "text": ["=literal", ""], "literal_na": ["N/A", "NA"]
        }, dtype=object)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "synthetic.xlsx"
            sheets = {"data": frame}
            export_workbook(target, sheets, {"data": {"date": "dd/mm/yyyy"}})
            result = verify_export(target, sheets)
            self.assertTrue(result["all_cells_match_retained_source"])


if __name__ == "__main__":
    unittest.main()
