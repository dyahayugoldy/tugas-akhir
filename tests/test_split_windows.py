"""WP4 acceptance and temporal counterfactual tests on synthetic data only."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from src.data.harmonize_workbooks import ROOT, sha256_file
from src.data.weekly_panel import save_csv
from src.features.calendar_lags import build_features
from src.features.split_windows import (
    assign_splits, build_windows, inverse_target, run, validate_config,
)
from src.features.window_dataset import WindowDataset, validate_run, verify_consumers
from src.utils.manifest import write_json


def fixture():
    config = json.loads((ROOT / "configs/split-window.v1.json").read_text())
    config["split"] = {"train": {"start": "2024-01-01", "end": "2024-03-31"},
                       "validation": {"start": "2024-04-01", "end": "2024-04-28"},
                       "test": {"start": "2024-04-29", "end": "2024-06-30"}}
    config["window"].update(encoder_weeks=2, horizon_weeks=1)
    config["loader"]["batch_size"] = 7
    panel_config = json.loads((ROOT / "configs/panel-weekly.v1.json").read_text())
    panel_config["coverage"]["periods"] = [{"source_year": 2024, "start": "2024-01-01", "end": "2024-06-30"}]
    panel_config["features"]["target_lags_weeks"] = [1, 2]
    contract = json.loads((ROOT / "configs/feature-contract.v3.json").read_text())
    contract["contract_version"] = "synthetic-panel-features"
    contract["window_design"] = {"development_start": "2024-01-01", "development_end": "2024-03-31",
                                  "encoder_weeks": 2, "horizon_weeks": 1}
    contract["feature_groups"] = {"static_categorical": ["country_key", "sales_group"],
                                   "known_future": panel_config["features"]["calendar_fields"],
                                   "observed_past": ["target_lag_1", "target_lag_2"]}
    registry = pd.DataFrame([
        {"series_id": "a" * 64, "country_key": "__UNKNOWN_COUNTRY__", "sales_group": "SYNTHETIC_A",
         "known_at": "2024-01-03", "activation_week": "2024-01-01"},
        {"series_id": "b" * 64, "country_key": "SYNTHETIC_NEW", "sales_group": "SYNTHETIC_B",
         "known_at": "2024-04-03", "activation_week": "2024-04-01"},
        {"series_id": "c" * 64, "country_key": "SYNTHETIC_SHORT", "sales_group": "SYNTHETIC_C",
         "known_at": "2024-06-26", "activation_week": "2024-06-24"},
    ])
    records = []
    for row in registry.itertuples():
        for number, week in enumerate(pd.date_range(row.activation_week, "2024-06-24", freq="7D")):
            records.append({"series_id": row.series_id, "week_start": week.strftime("%Y-%m-%d"),
                            "week_end": (week + pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
                            "available_at": (week + pd.Timedelta(days=7)).strftime("%Y-%m-%d"),
                            "order_quantity_item": str(number / 4)})
    panel = pd.DataFrame(records)
    feature = build_features(panel, "order_quantity_item", panel_config["features"])
    return panel, feature, registry, config, contract, panel_config


class SplitWindowTests(unittest.TestCase):
    def setUp(self):
        self.panel, self.features, self.registry, self.config, self.contract, self.panel_config = fixture()

    def build(self, panel=None, registry=None, config=None, contract=None):
        panel = self.panel if panel is None else panel
        features = build_features(panel, "order_quantity_item", self.panel_config["features"])
        return build_windows(panel, features, self.registry if registry is None else registry,
                             self.config if config is None else config,
                             self.contract if contract is None else contract, self.panel_config)

    def test_boundaries_shapes_dtypes_fractional_labels_and_inverse_scale(self):
        index, parts, arrays, prep, counts = self.build()
        self.assertFalse(parts.split.eq("excluded").any())
        self.assertEqual(counts["train"]["samples"], 9)
        self.assertEqual(counts["validation"]["samples"], 4)
        self.assertEqual(counts["test"]["samples"], 18)
        self.assertTrue(index.encoder_end.lt(index.origin_time).all())
        self.assertTrue(index.max_input_available_at.le(index.origin_time).all())
        self.assertTrue(index.target_available_at.gt(index.origin_time).all())
        self.assertFalse(index.sample_id.duplicated().any())
        for name in ("train", "validation", "test"):
            rows = index.loc[index.split.eq(name)]
            self.assertTrue(rows.target_start.ge(self.config["split"][name]["start"]).all())
            self.assertTrue(rows.target_end.le(self.config["split"][name]["end"]).all())
        self.assertEqual(arrays["encoder_observed"].shape, (31, 2, 3))
        self.assertEqual(arrays["decoder_lags"].shape, (31, 1, 2))
        self.assertEqual(arrays["static_categorical"].dtype, np.int64)
        self.assertEqual(arrays["encoder_observed_mask"].dtype, bool)
        self.assertEqual(arrays["y"].dtype, np.float32)
        self.assertTrue(np.modf(arrays["y"])[0].any())
        np.testing.assert_allclose(inverse_target(arrays["y_scaled"], prep), arrays["y"], atol=1e-6)

    def test_holdout_mutation_and_new_future_category_do_not_change_train_or_scaler(self):
        before = self.build()
        changed = self.panel.copy()
        changed.loc[changed.week_start.ge("2024-04-01"), "order_quantity_item"] = "987654.125"
        registry = self.registry.copy()
        registry.loc[registry.series_id.ne("a" * 64), "country_key"] = "SYNTHETIC_FUTURE"
        after = self.build(panel=changed, registry=registry)
        self.assertEqual(before[3], after[3])
        pd.testing.assert_frame_equal(before[0].loc[before[0].split.eq("train")], after[0].loc[after[0].split.eq("train")])
        for key in before[2]:
            np.testing.assert_array_equal(before[2][key][before[0].split.eq("train")], after[2][key][after[0].split.eq("train")])

    def test_own_label_not_in_inputs_but_available_holdout_history_is_used_later(self):
        before = self.build()
        changed = self.panel.copy()
        chosen = changed.series_id.eq("a" * 64) & changed.week_start.eq("2024-04-08")
        changed.loc[chosen, "order_quantity_item"] = "123456.75"
        after = self.build(panel=changed)
        row = (before[0].series_id.eq("a" * 64) & before[0].origin_time.eq("2024-04-08")).to_numpy()
        for key in ("encoder_observed", "encoder_known", "decoder_known", "decoder_lags", "static_categorical"):
            np.testing.assert_array_equal(before[2][key][row], after[2][key][row])
        self.assertNotEqual(before[2]["y"][row].item(), after[2]["y"][row].item())
        following = (before[0].series_id.eq("a" * 64) & before[0].origin_time.eq("2024-04-15")).to_numpy()
        self.assertFalse(np.array_equal(before[2]["encoder_observed"][following], after[2]["encoder_observed"][following]))
        early = before[0].origin_time.le("2024-04-08").to_numpy()
        np.testing.assert_array_equal(before[2]["encoder_observed"][early], after[2]["encoder_observed"][early])

    def test_unknown_country_category_is_distinct_from_unseen_encoder_id(self):
        index, _, arrays, prep, counts = self.build()
        vocab = prep["vocabularies"]["country_key"]
        self.assertEqual(vocab["__UNKNOWN__"], 0)
        self.assertGreater(vocab["__UNKNOWN_COUNTRY__"], 0)
        unseen = index.series_id.eq("b" * 64).to_numpy()
        self.assertTrue(arrays["static_categorical"][unseen].__eq__(0).all())
        self.assertEqual(counts["test"]["unknown_country_key"], 9)
        self.assertNotIn("SYNTHETIC_NEW", vocab)

    def test_cold_start_is_decided_per_origin_not_full_series_length(self):
        index, _, _, _, counts = self.build()
        new = index.loc[index.series_id.eq("b" * 64)]
        self.assertEqual(new.origin_time.min(), "2024-04-29")
        self.assertFalse(index.series_id.eq("c" * 64).any())
        self.assertGreater(counts["test"]["unknown_at_origin"], 0)
        self.assertEqual(len(new), 9)
        self.assertEqual(counts["validation"]["insufficient_encoder_history"], 1)

    def test_multistep_original_origin_masks_future_lags_and_crossing_targets(self):
        config, contract = copy.deepcopy(self.config), copy.deepcopy(self.contract)
        config["window"]["horizon_weeks"] = contract["window_design"]["horizon_weeks"] = 2
        index, _, arrays, _, counts = self.build(config=config, contract=contract)
        self.assertEqual(arrays["decoder_lags"].shape[1:], (2, 2))
        self.assertFalse(arrays["decoder_lag_mask"][:, 1, 0].any())
        self.assertTrue(arrays["decoder_lags"][:, 1, 0].__eq__(0).all())
        self.assertTrue(arrays["decoder_lag_mask"][:, 1, 1].all())
        self.assertGreater(counts["train"]["target_crosses_split"], 0)
        self.assertGreater(counts["test"]["incomplete_target_horizon"], 0)
        self.assertFalse(index.loc[index.split.eq("train"), "target_end"].gt("2024-03-31").any())

    def test_capture_delay_prevents_unavailable_encoder_targets(self):
        self.panel_config["availability"]["capture_delay_days"] = 1
        changed = self.panel.copy()
        changed["available_at"] = (pd.to_datetime(changed.available_at) + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")
        with self.assertRaisesRegex(ValueError, "Each split must contain eligible"):
            self.build(panel=changed)

    def test_reject_invalid_overlapping_nonweekly_or_outside_coverage_split(self):
        for part, key, date in (("validation", "start", "2024-03-25"), ("test", "start", "2024-04-30"),
                                ("test", "end", "2024-07-07"), ("train", "end", "2024-03-24")):
            broken = copy.deepcopy(self.config)
            broken["split"][part][key] = date
            with self.subTest(date=date), self.assertRaises(ValueError):
                validate_config(broken, self.contract, self.panel_config)
        broken = copy.deepcopy(self.config)
        broken["window"]["encoder_weeks"] += 1
        with self.assertRaisesRegex(ValueError, "frozen WP3"):
            validate_config(broken, self.contract, self.panel_config)

    def test_reject_duplicate_gapped_invalid_targets_and_tampered_features(self):
        cases = [pd.concat([self.panel, self.panel.iloc[[0]]], ignore_index=True), self.panel.drop(index=4)]
        for value in ("", "nan", "inf", "-1"):
            changed = self.panel.copy()
            changed.loc[0, "order_quantity_item"] = value
            cases.append(changed)
        for changed in cases:
            with self.assertRaises(ValueError):
                self.build(panel=changed)
        changed_features = self.features.copy()
        changed_features.loc[5, "target_lag_1"] = "123.125"
        with self.assertRaisesRegex(ValueError, "reconstruction mismatch"):
            build_windows(self.panel, changed_features, self.registry, self.config, self.contract, self.panel_config)

    def test_constant_scaler_and_no_target_rounding_or_imputation(self):
        panel = self.panel.copy()
        panel["order_quantity_item"] = "1.125"
        _, _, arrays, prep, _ = self.build(panel=panel)
        self.assertEqual(prep["target"]["scale"], self.config["preprocessing"]["constant_scale"])
        self.assertTrue(arrays["y"].__eq__(1.125).all())
        self.assertTrue(arrays["y_scaled"].__eq__(0).all())
        self.assertEqual(prep["statistical_imputation"], "none")
        registry = self.registry.copy()
        registry.loc[0, "country_key"] = "__UNKNOWN__"
        with self.assertRaisesRegex(ValueError, "Reserved encoder"):
            self.build(registry=registry)

    def workspace(self, root):
        base = root / "artifacts/input"
        base.mkdir(parents=True)
        (root / "configs").mkdir()
        (root / "dataset").mkdir()
        (root / "dataset/source.xlsx").write_bytes(b"synthetic private source sentinel")
        for path, value in (("panel/panel_weekly.csv.gz", self.panel), ("features/calendar_lags.csv.gz", self.features),
                             ("panel/series_registry.csv.gz", self.registry)):
            save_csv(base / path, value)
        write_json(base / "feature_contract.json", self.contract)
        write_json(base / "config.resolved.json", self.panel_config)
        manifest = {"stage": "WP3_weekly_panel", "manifest_id": "synthetic-panel", "ready_for_split": True,
                    "final_split_created": False, "counts": {"panel_rows": len(self.panel), "observed_series": len(self.registry)},
                    "outputs": {path.relative_to(base).as_posix(): sha256_file(path) for path in base.rglob("*") if path.is_file()},
                    "inputs_sha256": {"dataset/source.xlsx": sha256_file(root / "dataset/source.xlsx")},
                    "availability_assumption": self.panel_config["availability"], "coverage_assumption": "synthetic-coverage"}
        write_json(base / "data_manifest.json", manifest)
        write_json(root / "configs/current-data.json", {"active_panel_manifest": "artifacts/input/data_manifest.json"})
        config_path = root / "configs/split-window.v1.json"
        write_json(config_path, self.config)
        return config_path

    def test_end_to_end_reproducibility_three_consumers_checksums_and_seeded_shuffle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self.workspace(root)
            before = sha256_file(root / "dataset/source.xlsx")
            first, second = run(config_path, root=root), run(config_path, root=root)
            self.assertNotEqual(first, second)
            manifest = json.loads((first / "split_manifest.json").read_text())
            self.assertTrue(manifest["ready_for_models"])
            self.assertTrue(verify_consumers(first, manifest)["all_equal"])
            self.assertTrue(validate_run(first / "split_manifest.json", root=root, verify_inputs=True)["all_equal"])
            for name, checksum in manifest["outputs"].items():
                self.assertEqual(sha256_file(first / name), checksum)
            for name in ("tensors/train.npz", "tensors/validation.npz", "tensors/test.npz", "index/window_index.csv.gz",
                         "index/panel_split.csv.gz", "preprocessing.json", "tensor_schema.json", "loader_verification.json"):
                self.assertEqual(sha256_file(first / name), sha256_file(second / name))
            self.assertEqual(sha256_file(root / "dataset/source.xlsx"), before)
            dataset = WindowDataset(first / "split_manifest.json", "tft", "test")
            self.assertEqual(len(dataset), 18)
            batches = list(dataset.batches(7))
            self.assertEqual([len(batch["metadata"]["sample_id"]) for batch in batches], [7, 7, 4])
            self.assertNotIn("y", batches[0]["inputs"])
            a = np.concatenate([batch["metadata"]["sample_id"] for batch in dataset.batches(5, shuffle=True, seed=42)])
            b = np.concatenate([batch["metadata"]["sample_id"] for batch in dataset.batches(5, shuffle=True, seed=42)])
            np.testing.assert_array_equal(a, b)
            self.assertEqual(set(a), set(dataset.arrays["sample_id"]))
            with self.assertRaises(ValueError):
                WindowDataset(first / "split_manifest.json", "other", "test")
            with self.assertRaises(ValueError):
                list(dataset.batches(7, shuffle=True))
            (first / "tensors/test.npz").write_bytes(b"tampered synthetic")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                WindowDataset(first / "split_manifest.json", "tft", "test")

    def test_tampered_ancestors_unready_panel_and_public_output_are_rejected(self):
        for scenario in ("source", "output", "unready", "public"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if scenario == "public":
                    self.config["output_root"] = "reports/unsafe"
                path = self.workspace(root)
                if scenario == "source":
                    (root / "dataset/source.xlsx").write_bytes(b"tampered")
                elif scenario == "output":
                    (root / "artifacts/input/panel/panel_weekly.csv.gz").write_bytes(b"tampered")
                elif scenario == "unready":
                    mf = root / "artifacts/input/data_manifest.json"
                    payload = json.loads(mf.read_text())
                    payload["ready_for_split"] = False
                    mf.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    run(path, root=root)


if __name__ == "__main__":
    unittest.main()
