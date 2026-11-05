"""Strict missingness boundaries and column-only deletion on synthetic data."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.data.clean_workbooks import export_workbook
from src.data.missing_values import drop_columns, profile_frame, verify_projection


class MissingValueTests(unittest.TestCase):
    def test_strict_threshold_excludes_zero_and_exactly_half_percent(self):
        frame = pd.DataFrame({"complete": [1] * 2000,
            "below": [None] * 9 + [1] * 1991,
            "at": [None] * 10 + [1] * 1990,
            "above": [None] * 11 + [1] * 1989}, dtype=object)
        original = frame.copy(deep=True)
        result, profile = drop_columns(frame, "0.5")
        self.assertEqual([item["column"] for item in profile if item["eligible_below_threshold"]], ["below"])
        pd.testing.assert_frame_equal(result, frame[["complete", "at", "above"]])
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual(len(result), 2000)

    def test_blank_definition_preserves_zero_false_and_literal_na_labels(self):
        frame = pd.DataFrame({"mixed": [None, "", "  ", 0, False, "NA", "N/A"]}, dtype=object)
        profile, _ = profile_frame(frame, "50")
        self.assertEqual(profile[0]["missing_count"], 3)
        self.assertAlmostEqual(profile[0]["missing_percent"], 300 / 7)

    def test_projection_is_idempotent_and_keeps_original_row_indices(self):
        frame = pd.DataFrame({"rare": [None] + [1] * 999, "key": list(range(1000))}, index=range(5, 1005), dtype=object)
        once, _ = drop_columns(frame, "0.5")
        twice, _ = drop_columns(once, "0.5")
        pd.testing.assert_frame_equal(once, twice)
        self.assertEqual(once.index.tolist(), frame.index.tolist())

    def test_export_keeps_duplicate_rows_created_by_column_projection(self):
        frame = pd.DataFrame({"quantity": [1, 1],
            "date": [datetime(2025, 1, 2)] * 2, "empty": [None, ""]}, dtype=object)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.xlsx"
            sheets = {"data": frame}
            export_workbook(path, sheets, {"data": {"date": "dd/mm/yyyy"}})
            result = verify_projection(path, sheets)
            self.assertTrue(result["row_count_preserved"])
            self.assertTrue(result["all_retained_cells_match_source"])


if __name__ == "__main__":
    unittest.main()
