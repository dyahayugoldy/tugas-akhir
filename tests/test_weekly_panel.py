"""WP3 acceptance and leakage tests using only synthetic transactions."""

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.data.harmonize_workbooks import ROOT, sha256_file
from src.data.weekly_panel import (
    build_panel, choose_window_design, complete_calendar, descriptive_eda,
    prepare_transactions, run, save_csv, validate_config,
)
from src.features.calendar_lags import build_features, features_at_origin


def settings():
    config = json.loads((ROOT / "configs/panel-weekly.v1.json").read_text(encoding="utf-8"))
    config["coverage"]["periods"] = [{"source_year": 2024, "start": "2024-01-01", "end": "2024-04-03"}]
    config["features"]["target_lags_weeks"] = [1, 2]
    config["window_design"].update(development_start="2024-01-01", development_end="2024-03-31", encoder_candidates_weeks=[2, 4])
    contract = json.loads((ROOT / "configs/feature-contract.v3.json").read_text(encoding="utf-8"))
    return config, contract


def transactions(contract, updates=None):
    if updates is None:
        updates = [
            {"kp_date": "2024-01-03", "order_quantity_item": "1.5"},
            {"kp_date": "2024-01-07", "order_quantity_item": "2.25"},
            {"kp_date": "2024-01-15", "order_quantity_item": "4.125"},
            {"kp_date": "2024-01-08", "order_quantity_item": "10", "sales_group": "SYNTHETIC_B"},
            {"kp_date": "2024-04-03", "order_quantity_item": "7.5"},
        ]
    rows = []
    for index, update in enumerate(updates):
        row = {column["field"]: "" for column in contract["columns"]}
        row.update(source_year="2024", source_file="source.XLSX", source_sheet="synthetic", source_row_number=str(index + 2),
                   quality_status="valid", quality_flags="", wp2_quality_holds="", wp2_country_was_missing="false",
                   country_key="SYNTHETIC_COUNTRY", sales_group="SYNTHETIC_A", kp_date="2024-01-01",
                   order_quantity_item="1", document_currency="USD", net_price="1", net_value_item="1")
        row.update(update)
        rows.append(row)
    return pd.DataFrame(rows)


class WeeklyPanelTests(unittest.TestCase):
    def setUp(self):
        self.config, self.contract = settings()
        self.frame = transactions(self.contract)

    def pipeline(self, frame=None, config=None):
        config = self.config if config is None else config
        prepared = prepare_transactions(self.frame if frame is None else frame, self.contract, config)
        observed, panel, registry = build_panel(prepared, self.contract, config)
        features = build_features(panel, self.contract["target"]["field"], config["features"])
        return prepared, observed, panel, registry, features

    def test_configuration_rejects_leakage_fields_and_coverage_gaps(self):
        validate_config(self.config, self.contract)
        broken = copy.deepcopy(self.contract)
        broken["series"]["fields"] = ["country_key", "crd_date"]
        with self.assertRaisesRegex(ValueError, "static categorical"):
            validate_config(self.config, broken)
        broken = copy.deepcopy(self.config)
        broken["coverage"]["periods"].append({"source_year": 2025, "start": "2025-01-02", "end": "2025-12-31"})
        with self.assertRaisesRegex(ValueError, "contiguous"):
            validate_config(broken, self.contract)
        broken = copy.deepcopy(self.config)
        broken["features"]["target_lags_weeks"] = [0, 1]
        with self.assertRaisesRegex(ValueError, "positive integers"):
            validate_config(broken, self.contract)

    def test_exact_fractional_aggregation_and_end_of_series_zero_weeks(self):
        prepared, observed, panel, registry, _ = self.pipeline()
        series = registry.loc[registry.sales_group.eq("SYNTHETIC_A"), "series_id"].iloc[0]
        one = panel.loc[panel.series_id.eq(series)].set_index("week_start")
        self.assertEqual(one.loc["2024-01-01", "order_quantity_item"], "3.75")
        self.assertEqual(one.loc["2024-01-08", "order_quantity_item"], "0")
        self.assertEqual(one.loc["2024-01-15", "order_quantity_item"], "4.125")
        self.assertEqual(one.loc["2024-03-25", "order_quantity_item"], "0")
        self.assertEqual(one.loc["2024-01-08", "wp3_zero_filled"], "true")
        self.assertEqual(one.loc["2024-01-01", "wp3_zero_filled"], "false")
        self.assertFalse(panel.duplicated(["series_id", "week_start"]).any())
        self.assertEqual(sum(map(Decimal, panel.order_quantity_item)), Decimal("17.875"))
        self.assertEqual(sum(map(Decimal, observed.order_quantity_item)), Decimal("25.375"))
        report = descriptive_eda(prepared, observed, panel, registry, self.contract, self.config)
        self.assertEqual(report["counts"]["partial_week_transactions_held_out"], 1)
        self.assertEqual(report["counts"]["absent_active_series_weeks_after_regularization"], 0)

    def test_partial_boundary_weeks_are_audited_not_silently_zero_filled(self):
        self.config["coverage"]["periods"][0]["start"] = "2024-01-03"
        self.config["window_design"]["development_start"] = "2024-01-08"
        _, observed, panel, _, _ = self.pipeline()
        self.assertEqual(panel.week_start.min(), "2024-01-08")
        self.assertEqual(panel.week_start.max(), "2024-03-25")
        partial = observed.loc[observed.complete_coverage_week.eq("false")]
        self.assertEqual(set(partial.week_start), {"2024-01-01", "2024-04-01"})
        self.assertEqual(int(partial.transaction_count.sum()), 3)

    def test_no_preactivation_rows_short_and_late_series_are_retained(self):
        self.config["coverage"]["periods"][0]["end"] = "2024-06-30"
        updates = [{"kp_date": "2024-01-03"}, {"kp_date": "2024-06-26", "sales_group": "SYNTHETIC_NEW"}]
        _, _, panel, registry, features = self.pipeline(transactions(self.contract, updates))
        series = registry.loc[registry.sales_group.eq("SYNTHETIC_NEW"), "series_id"].iloc[0]
        late = panel.loc[panel.series_id.eq(series)]
        self.assertEqual(late.week_start.tolist(), ["2024-06-24"])
        self.assertEqual(registry.loc[registry.series_id.eq(series), "known_at"].iloc[0], "2024-06-26")
        self.assertTrue(features.loc[features.series_id.eq(series), "target_lag_1"].eq("").all())
        prior_origin = features_at_origin(features, "2024-06-24", [1, 2], registry)
        self.assertFalse(prior_origin.series_id.eq(series).any())

    def test_future_mutations_and_future_categories_leave_past_unchanged(self):
        self.config["coverage"]["periods"][0]["end"] = "2024-06-30"
        _, _, before, _, features_before = self.pipeline()
        changed = self.frame.copy()
        changed.loc[changed.kp_date.gt("2024-03-31"), "order_quantity_item"] = "999999.125"
        changed = pd.concat([changed, transactions(self.contract, [{"kp_date": "2024-05-06", "sales_group": "SYNTHETIC_FUTURE"}])], ignore_index=True)
        _, _, after, _, features_after = self.pipeline(changed)
        cutoff = "2024-03-25"
        pd.testing.assert_frame_equal(before.loc[before.week_start.le(cutoff)].reset_index(drop=True),
                                      after.loc[after.week_start.le(cutoff)].reset_index(drop=True))
        pd.testing.assert_frame_equal(features_before.loc[features_before.week_start.le(cutoff)].reset_index(drop=True),
                                      features_after.loc[features_after.week_start.le(cutoff)].reset_index(drop=True))
        self.assertEqual(choose_window_design(before, self.config), choose_window_design(after, self.config))

    def test_lags_are_series_local_and_respect_fixed_multistep_origin(self):
        _, _, _, registry, feature = self.pipeline()
        series = registry.loc[registry.sales_group.eq("SYNTHETIC_A"), "series_id"].iloc[0]
        one = feature.loc[feature.series_id.eq(series)].set_index("week_start")
        self.assertEqual(one.loc["2024-01-08", "target_lag_1"], "3.75")
        self.assertEqual(one.loc["2024-01-15", "target_lag_1"], "0")
        self.assertEqual(one.loc["2024-01-15", "target_lag_2"], "3.75")
        fixed = features_at_origin(feature, "2024-01-15", [1, 2], registry)
        future = fixed.loc[fixed.series_id.eq(series) & fixed.week_start.eq("2024-01-22")].iloc[0]
        self.assertEqual(future.target_lag_1, "")
        self.assertEqual(future.target_lag_1_observed, "false")
        self.assertEqual(future.target_lag_2, "0")
        self.assertNotIn("order_quantity_item", feature.columns)
        self.assertNotIn("crd_date", feature.columns)

    def test_late_availability_is_masked_and_lag_null_is_not_zero(self):
        self.config["availability"]["capture_delay_days"] = 1
        _, _, _, _, feature = self.pipeline()
        self.assertTrue(feature.target_lag_1.eq("").all())
        self.assertTrue(feature.target_lag_1_observed.eq("false").all())
        self.assertTrue(feature.target_lag_2.ne("").any())

    def test_invalid_targets_and_nonregular_calendars_are_rejected(self):
        for target in ["", "NaN", "Infinity", "-1"]:
            broken = self.frame.copy()
            broken.loc[0, "order_quantity_item"] = target
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.pipeline(broken)
        _, _, panel, _, _ = self.pipeline()
        panel = panel.drop(panel.index[1])
        with self.assertRaisesRegex(ValueError, "regular weekly"):
            build_features(panel, "order_quantity_item", self.config["features"])

    def test_calendar_year_boundary_and_tuple_identity_are_unambiguous(self):
        self.config["coverage"]["periods"] = [{"source_year": 2024, "start": "2024-01-01", "end": "2024-12-31"},
                                                   {"source_year": 2025, "start": "2025-01-01", "end": "2025-01-05"}]
        updates = [{"kp_date": "2024-12-30", "country_key": "A|B", "sales_group": "C"},
                   {"kp_date": "2025-01-02", "source_year": "2025", "country_key": "A", "sales_group": "B|C"}]
        _, _, panel, registry, feature = self.pipeline(transactions(self.contract, updates))
        self.assertEqual(len(registry.series_id.unique()), 2)
        self.assertEqual(set(panel.week_start), {"2024-12-30"})
        self.assertTrue(feature.iso_year.eq(2025).all())
        self.assertTrue(feature.calendar_year.eq(2024).all())
        self.assertTrue(feature.iso_week.eq(1).all())
        self.assertEqual(complete_calendar(self.config)[-1], pd.Timestamp("2024-12-30"))

    def make_workspace(self, root):
        (root / "configs").mkdir()
        (root / "dataset").mkdir()
        input_dir = root / "artifacts/input"
        input_dir.mkdir(parents=True)
        source = root / "dataset/source.XLSX"
        source.write_bytes(b"synthetic source only")
        cleaning = input_dir / "cleaning_manifest.json"
        cleaning.write_text(json.dumps({"config": {"sources": ["dataset/source.XLSX"]}}))
        canonical = input_dir / "canonical_manifest.json"
        canonical.write_text(json.dumps({"input_cleaning_manifest": "artifacts/input/cleaning_manifest.json",
                                         "input_cleaning_manifest_sha256": sha256_file(cleaning),
                                         "sources": [{"source_file": "source.XLSX", "source_sha256": sha256_file(source)}]}))
        self.frame.to_csv(input_dir / "selected_transactions.csv.gz", index=False)
        (input_dir / "feature_contract.json").write_text(json.dumps(self.contract))
        selection = json.loads((ROOT / "configs/business-selection.v3.json").read_text())
        (input_dir / "config.resolved.json").write_text(json.dumps(selection))
        outputs = {path.name: sha256_file(path) for path in input_dir.iterdir() if path.name not in {cleaning.name, canonical.name}}
        manifest = {"manifest_id": "synthetic-wp2", "stage": "WP2_business_selection", "ready_for_panel": True, "blockers": [],
                    "input_manifest": "artifacts/input/canonical_manifest.json", "inputs_sha256": {"artifacts/input/canonical_manifest.json": sha256_file(canonical)},
                    "outputs": outputs, "row_count": len(self.frame)}
        (input_dir / "data_manifest.json").write_text(json.dumps(manifest))
        (root / "configs/current-data.json").write_text(json.dumps({"active_business_data_manifest": "artifacts/input/data_manifest.json"}))
        (root / self.config["feature_contract"]).write_text(json.dumps(self.contract))
        config_path = root / "configs/panel-weekly.v1.json"
        config_path.write_text(json.dumps(self.config))
        return config_path

    def test_end_to_end_manifest_exact_roundtrip_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.make_workspace(root)
            original_source = sha256_file(root / "dataset/source.XLSX")
            first, second = run(path, root=root), run(path, root=root)
            self.assertNotEqual(first, second)
            manifest = json.loads((first / "data_manifest.json").read_text())
            self.assertTrue(manifest["ready_for_split"])
            self.assertFalse(manifest["final_split_created"])
            self.assertTrue(manifest["verification"]["exact_target_conservation"])
            self.assertEqual(manifest["encoder_weeks"], 2)
            for name in ["panel/panel_weekly.csv.gz", "aggregated/observed_weekly.csv.gz", "features/calendar_lags.csv.gz", "panel_report.json", "window_design.json", "target_eda.json"]:
                self.assertEqual(sha256_file(first / name), sha256_file(second / name))
            for name, checksum in manifest["outputs"].items():
                self.assertEqual(sha256_file(first / name), checksum)
            self.assertEqual(sha256_file(root / "dataset/source.XLSX"), original_source)

    def test_input_tampering_and_unresolved_holds_are_rejected(self):
        for scenario in ["raw", "output", "hold", "unready"]:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                if scenario == "hold":
                    self.frame.loc[0, "wp2_quality_holds"] = "synthetic_hold"
                path = self.make_workspace(root)
                if scenario == "raw":
                    (root / "dataset/source.XLSX").write_bytes(b"tampered")
                elif scenario == "output":
                    (root / "artifacts/input/selected_transactions.csv.gz").write_bytes(b"tampered")
                elif scenario == "unready":
                    mf = root / "artifacts/input/data_manifest.json"
                    payload = json.loads(mf.read_text())
                    payload["ready_for_panel"] = False
                    mf.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    run(path, root=root)

    def test_public_output_is_rejected(self):
        self.config["output_root"] = "reports/unsafe"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "private artifacts"):
                run(self.make_workspace(root), root=root)

    def test_roundtrip_error_does_not_expose_private_values(self):
        private_value = "SYNTHETIC_PRIVATE_CATEGORY"
        frame = pd.DataFrame({"category": [private_value]})
        with tempfile.TemporaryDirectory() as folder:
            with patch("src.data.weekly_panel.pd.read_csv", return_value=pd.DataFrame({"category": ["changed"]})):
                with self.assertRaisesRegex(ValueError, "private run artifacts") as caught:
                    save_csv(Path(folder) / "synthetic.csv.gz", frame)
            self.assertNotIn(private_value, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
