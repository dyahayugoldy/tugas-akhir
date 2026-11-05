"""WP1 canonicalization contracts use synthetic workbooks only."""

from __future__ import annotations

import csv
import gzip
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
import pandas as pd

from src.data.harmonize_workbooks import (
    HarmonizationConfigError,
    assess_row,
    canonicalize_value,
    run,
    sha256_file,
    validate_config,
)


def synthetic_config() -> dict:
    return {
        "config_version": "synthetic-harmonize-v1",
        "canonical_schema_version": "synthetic-canonical-v1",
        "active_cleaning_manifest": "artifacts/cleaning_manifest.json",
        "output_root": "artifacts/data/canonical",
        "source_years": {"source.xlsx": 2025},
        "sheet_policy": {"mode": "all_sheets"},
        "schema_assertions": {
            "expected_column_count": 4,
            "require_identical_headers": True,
            "allow_extra_columns": False,
        },
        "column_definitions": [
            {"source_name": "KP Date", "canonical_name": "kp_date", "logical_type": "date"},
            {"source_name": "CRD Date", "canonical_name": "crd_date", "logical_type": "date"},
            {"source_name": "Quantity", "canonical_name": "quantity", "logical_type": "numeric"},
            {"source_name": "Currency", "canonical_name": "currency", "logical_type": "string"},
        ],
        "quality_policy": {
            "primary_date_field": "kp_date",
            "secondary_date_field": "crd_date",
            "numeric_fields": ["quantity"],
            "currency_field": "currency",
            "primary_date_missing_or_invalid": "rejected",
            "secondary_date_missing_or_invalid": "flagged",
            "numeric_missing_or_invalid": "flagged",
            "negative_numeric": "flagged",
            "zero_numeric": "valid_counted_only",
            "blank_currency": "flagged",
            "crd_gap": {
                "reference_field": "kp_date",
                "comparison_field": "crd_date",
                "rule": "comparison_minus_reference_days_gt_max_forward_days",
                "max_forward_days": 730,
                "action": "flag_only",
                "feature_availability": "unverified_and_excluded_from_model_features",
            },
            "row_retention": "retain_all_rows_in_canonical_artifact",
        },
        "privacy_policy": {"row_level_artifacts": "private_under_artifacts", "reports_must_not_include": []},
    }


class HarmonizeWorkbookTests(unittest.TestCase):
    def test_canonicalize_preserves_valid_zero_and_marks_invalid_values(self):
        self.assertEqual(canonicalize_value(0, "numeric"), ("0", Decimal("0"), "valid"))
        self.assertEqual(canonicalize_value("not-a-number", "numeric"), (None, None, "invalid"))
        value, parsed, state = canonicalize_value(datetime(2025, 2, 3, 17, 30), "date")
        self.assertEqual(value, "2025-02-03")
        self.assertEqual(parsed, pd.Timestamp("2025-02-03"))
        self.assertEqual(state, "valid")

    def test_row_status_rejects_invalid_kp_and_only_flags_large_crd_gap(self):
        policy = synthetic_config()["quality_policy"]
        status, flags, gap = assess_row(
            {"kp_date": "valid", "crd_date": "valid", "quantity": "valid", "currency": "valid"},
            {
                "kp_date": pd.Timestamp("2023-01-01"),
                "crd_date": pd.Timestamp("2025-01-02"),
                "quantity": Decimal("1"),
                "currency": "USD",
            },
            policy,
        )
        self.assertEqual(status, "flagged")
        self.assertEqual(gap, 732)
        self.assertEqual(flags, ["crd_date_after_kp_date_over_730_days"])

        status, flags, gap = assess_row(
            {"kp_date": "blank", "crd_date": "valid", "quantity": "valid", "currency": "blank"},
            {"kp_date": None, "crd_date": pd.Timestamp("2025-01-02"), "quantity": Decimal("0"), "currency": None},
            policy,
        )
        self.assertEqual(status, "rejected")
        self.assertIsNone(gap)
        self.assertEqual(flags, ["kp_date_blank", "currency_blank"])

    def test_config_rejects_undeclared_quality_field(self):
        config = synthetic_config()
        config["quality_policy"]["currency_field"] = "not_in_schema"
        with self.assertRaises(HarmonizationConfigError):
            validate_config(config)

    def test_run_retains_all_rows_and_writes_private_audit_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_dir = root / "dataset"
            source_dir.mkdir()
            source_path = source_dir / "source.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "Sheet1"
            sheet.append(["KP Date", "CRD Date", "Quantity", "Currency"])
            sheet.append(["2025-01-01", "2025-03-01", 2, "USD"])
            sheet.append(["2023-01-01", "2025-01-02", -1, "USD"])
            sheet.append([None, "2025-01-02", 0, None])
            workbook.save(source_path)
            source_hash = sha256_file(source_path)

            artifacts = root / "artifacts"
            artifacts.mkdir()
            cleaned_path = artifacts / "source_cleaned.xlsx"
            shutil.copy2(source_path, cleaned_path)
            cleaning_manifest = {
                "run_id": "synthetic-clean-v1",
                "config": {"sources": ["dataset/source.xlsx"]},
                "sources": [
                    {
                        "source_file": "source.xlsx",
                        "source_sha256": source_hash,
                        "source_unchanged": True,
                        "output_file": "source_cleaned.xlsx",
                        "output_sha256": sha256_file(cleaned_path),
                        "sheets": [{"sheet": "Sheet1", "rows_after": 3}],
                    }
                ],
            }
            (artifacts / "cleaning_manifest.json").write_text(json.dumps(cleaning_manifest), encoding="utf-8")
            config_path = root / "harmonize.json"
            config_path.write_text(json.dumps(synthetic_config()), encoding="utf-8")

            output = run(config_path, root=root)
            report = json.loads((output / "data_quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["combined_quality_status_counts"], {"flagged": 1, "rejected": 1, "valid": 1})
            self.assertEqual(report["source_reports"][0]["quality"]["rows_read"], 3)
            self.assertEqual(report["source_reports"][0]["quality"]["rows_retained"], 3)
            self.assertEqual(sha256_file(source_path), source_hash)

            with gzip.open(output / "canonical_transactions.csv.gz", "rt", encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["quality_status"] for row in rows], ["valid", "flagged", "rejected"])
            self.assertEqual(rows[1]["quality_flags"], "quantity_negative|crd_date_after_kp_date_over_730_days")
            self.assertEqual(rows[2]["quality_flags"], "kp_date_blank|currency_blank")


if __name__ == "__main__":
    unittest.main()
