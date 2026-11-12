"""Synthetic WP7 integration, frozen lineage and error-analysis regression tests."""

import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.data.harmonize_workbooks import ROOT, load_json, sha256_file
from src.evaluation import test_evaluation as evaluation
from src.features.split_windows import run as window_run
from src.models.adapter import ForecastAdapter, ModelData
from src.utils.manifest import write_json
import test_split_windows as wp4


class EvaluationTests(unittest.TestCase):
    def config(self):
        return load_json(ROOT / "configs/evaluation.wp7.v1.json")

    def workspace(self, root):
        fixture = wp4.SplitWindowTests()
        fixture.setUp()
        fixture.config["window"]["horizon_weeks"] = 2
        fixture.contract["window_design"]["horizon_weeks"] = 2
        split = window_run(fixture.workspace(root), root=root) / "split_manifest.json"
        for name in ("requirements.txt", "configs/run-manifest.schema.json", "docs/wp7-evaluation-protocol.md"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        config, entries = self.config(), []
        for name in config["models"]:
            data = ModelData(split, name, "test")
            architecture = load_json(ROOT / "configs/models.wp6.v1.json")["models"][name]
            architecture.update(d_model=8, n_heads=1, embedding_dim=2, dropout=0.0)
            if name != "tft":
                architecture.update(encoder_layers=1, decoder_layers=1, d_ff=16)
            for seed in config["seeds"]:
                base = root / "artifacts/runs" / f"synthetic-{name}-{seed}"
                base.mkdir(parents=True)
                # Untrained synthetic checkpoints test the evaluation lifecycle only.
                model = ForecastAdapter(name, data.contract, architecture, runtime=config["runtime"], seed=seed)
                model.fit_record = {"test_used": False, "synthetic_only": True}
                model.save(base / "checkpoint.pt")
                upstream = {"model_name": name, "seed": seed,
                            "timing": {"training_seconds": 1.25},
                            "effective_config": {"runtime": config["runtime"], "metrics": config["metrics"],
                                                 "training": {"batch_size": config["batch_size"]},
                                                 "split_manifest_path": split.relative_to(root).as_posix()},
                            "wp6": {"phase": "final_training", "source_code_sha256": {},
                                    "training": model.fit_record, "split_manifest_sha256": sha256_file(split)}}
                path = base / "run_manifest.json"
                write_json(path, upstream)
                entries.append({"model_name": name, "seed": seed, "manifest_path": path.relative_to(root).as_posix(),
                                "sha256": sha256_file(path)})
        receipt = {"stage": "WP6_certified_cohort", "test_predictions": 0, "final_runs": entries}
        cohort = root / "artifacts/runs/synthetic-closure/cohort.json"
        cohort.parent.mkdir()
        write_json(cohort, receipt)
        config.update(cohort_path=cohort.relative_to(root).as_posix(), cohort_sha256=sha256_file(cohort))
        config_path = root / "configs/evaluation.wp7.v1.json"
        write_json(config_path, config)
        return config_path, split, receipt

    def test_full_grid_multistep_roundtrip_and_tamper_detection_without_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            config_path, split, receipt = self.workspace(root)
            before = sha256_file(split)
            # The two WP6-auditor boundaries are replaced only for synthetic upstream records.
            with patch.object(evaluation, "verify_cohort", return_value=receipt), \
                    patch.object(evaluation, "verify_experiment", side_effect=lambda p, **kw: load_json(p)), \
                    patch.object(evaluation, "source_info", return_value=("a" * 40, True)), \
                    patch.object(ForecastAdapter, "fit", side_effect=AssertionError("WP7 must never fit")):
                path = evaluation.run(config_path, root=root)
                verified = evaluation.verify(path, root=root)
                self.assertEqual(len(verified["runs"]), 9)
                manifests = [load_json(root / e["manifest_path"]) for e in verified["runs"]]
                self.assertEqual(len({m["wp7"]["test_digest"] for m in manifests}), 1)
                self.assertTrue(all(m["timing"]["training_seconds"] == 0 for m in manifests))
                self.assertEqual(sha256_file(split), before)
                sample = pd.read_csv(root / manifests[0]["artifacts"]["predictions"])
                self.assertEqual(set(sample.horizon_step), {1, 2})
                self.assertTrue(sample.groupby("sample_id").size().eq(2).all())
                global_metrics = load_json(root / manifests[0]["artifacts"]["metrics"])["global"]
                self.assertEqual(global_metrics["count"], len(sample))
                self.assertEqual(global_metrics["mape_count"], len(sample))
                target = root / manifests[0]["artifacts"]["predictions"]
                target.write_bytes(target.read_bytes() + b"synthetic tamper")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    evaluation.verify_run(target.parent / "run_manifest.json", root=root)

    def test_grid_and_test_policy_rejected_before_inference(self):
        config = self.config()
        evaluation.validate_config(config)
        pairs = [{"model_name": name, "seed": seed} for name in config["models"] for seed in config["seeds"]]
        evaluation.validate_grid(pairs, config)
        with self.assertRaisesRegex(ValueError, "grid"):
            evaluation.validate_grid(pairs[:-1] + [pairs[0]], config)
        for key, value in (("output_root", "reports/public"), ("prediction_policy", "clip_to_zero"),
                           ("seeds", [42]), ("metrics", {"smape_epsilon": 1.0})):
            changed = copy.deepcopy(config)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                evaluation.validate_config(changed)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with self.assertRaises(ValueError):
                evaluation.private_path(root, "reports/unsafe")

    def test_zero_sparse_small_groups_and_negative_forecasts_are_visible(self):
        config = self.config()
        config["error_analysis"].update(minimum_group_samples=2, minimum_group_series=1)
        frame = pd.DataFrame({"series_id": ["a", "a", "a"], "actual": [0.0, 0.0, 0.0],
                              "prediction": [-1.0, 0.0, 2.0], "residual": [-1.0, 0.0, 2.0],
                              "horizon_step": [1, 1, 1], "target_time": ["2025-01-06", "2025-01-13", "2025-01-20"]})
        categories = pd.DataFrame({"series_id": ["a"], "country_key": ["SYNTHETIC"], "sales_group": ["SYNTHETIC"],
                                   "train_zero_fraction": [1.0], "sparsity_group": ["sparse_train"]})
        analysis = evaluation.analyze(frame, categories, {"target_tail_threshold": 1.0}, config)
        self.assertEqual(analysis["negative_predictions"], 1)
        self.assertEqual(analysis["actual_zero_count"], 3)
        self.assertEqual(analysis["residual"]["lag1_adjacent_pairs"], 2)
        metrics = analysis["groups"]["sparsity_group"][0]["metrics"]
        self.assertIsNone(metrics["mape"])
        self.assertIsNone(metrics["r2"])
        self.assertEqual(metrics["mape_count"], 0)
        config["error_analysis"]["minimum_group_samples"] = 4
        suppressed = evaluation.analyze(frame, categories, {"target_tail_threshold": 1.0}, config)
        self.assertTrue(suppressed["groups"]["series_id"][0]["suppressed_small_group"])
        self.assertIsNone(suppressed["groups"]["series_id"][0]["metrics"])

    def test_diagnostic_thresholds_ignore_future_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            fixture = wp4.SplitWindowTests()
            fixture.setUp()
            split = window_run(fixture.workspace(root), root=root) / "split_manifest.json"
            categories, thresholds = evaluation.build_context(split, root, self.config())
            self.assertEqual(thresholds["train_rows"], 13)
            self.assertEqual(categories.loc[categories.series_id.eq("b" * 64), "sparsity_group"].item(), "no_train_history")
            panel_path = root / "artifacts/input/panel/panel_weekly.csv.gz"
            original = pd.read_csv(panel_path, dtype=str)
            original.loc[original.week_start.ge("2024-04-01"), "order_quantity_item"] = "999999"
            # Re-seal a synthetic parent solely to test train-only diagnostic computation.
            original.to_csv(panel_path, index=False, compression="gzip")
            parent_path = root / "artifacts/input/data_manifest.json"
            parent = load_json(parent_path)
            parent["outputs"]["panel/panel_weekly.csv.gz"] = sha256_file(panel_path)
            parent_path.unlink()
            write_json(parent_path, parent)
            changed_categories, changed_thresholds = evaluation.build_context(split, root, self.config())
            self.assertEqual(thresholds, changed_thresholds)
            pd.testing.assert_frame_equal(categories, changed_categories)


if __name__ == "__main__":
    unittest.main()
