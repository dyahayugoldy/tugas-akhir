"""Single-run dispatch and pre-training lineage checks, without SAP or training."""

import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.data.harmonize_workbooks import ROOT, load_json
from src.models import experiment


class SingleRunTests(unittest.TestCase):
    def setUp(self):
        self.config_path = ROOT / "configs/models.wp6.v1.json"
        self.config = load_json(self.config_path)

    def test_dispatches_only_selected_model_and_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            contract, digests = {"manifest_id": "synthetic"}, {"train": "a", "validation": "b"}
            train = SimpleNamespace(contract=contract)
            validation = object()
            manifest = {"model_name": "nspace_transformer", "wp6": {
                "phase": "tuning", "split_manifest_sha256": "hash", "data_digests": digests},
                "effective_config": {**copy.deepcopy(self.config), "data_contract": contract}}
            tuning_path = root / "artifacts/runs/synthetic/run_manifest.json"
            final_path = root / "artifacts/runs/synthetic-final/run_manifest.json"
            data = {"nspace_transformer": (train, validation)}
            prepared = ({}, root / "split.json", {}, "hash", data, digests)
            with patch.object(experiment, "verify_experiment", return_value=manifest), \
                    patch.object(experiment, "_source_hashes", return_value={}), \
                    patch.object(experiment, "_prepare_data", return_value=prepared), \
                    patch.object(experiment, "_make_final_run", return_value=final_path) as fit:
                actual = experiment.train_one(self.config_path, "nspace_transformer", 123,
                                              tuning_path, root=root)
                self.assertEqual(actual, final_path)
                fit.assert_called_once()
                self.assertEqual(fit.call_args.args[2:5], ("nspace_transformer", 123, tuning_path))
                self.assertIs(fit.call_args.args[5], train)
                # A changed split is rejected before another fit can start.
                manifest["wp6"]["split_manifest_sha256"] = "different"
                with self.assertRaisesRegex(ValueError, "same immutable data"):
                    experiment.train_one(self.config_path, "nspace_transformer", 123,
                                         tuning_path, root=root)
                self.assertEqual(fit.call_count, 1)

    def test_invalid_model_or_seed_rejected_before_data_or_training(self):
        with patch.object(experiment, "_prepare_data") as prepare, \
                patch.object(experiment, "_make_final_run") as fit:
            for name, seed in (("tft", 999), ("unknown_model", 42)):
                with self.subTest(name=name, seed=seed), self.assertRaisesRegex(ValueError, "configured model"):
                    experiment.train_one(self.config_path, name, seed, "artifacts/runs/missing.json")
            prepare.assert_not_called()
            fit.assert_not_called()

    def test_wrong_model_or_modified_protocol_rejected_before_data_or_training(self):
        manifest = {"model_name": "tft", "wp6": {"phase": "tuning"},
                    "effective_config": copy.deepcopy(self.config)}
        with patch.object(experiment, "verify_experiment", return_value=manifest), \
                patch.object(experiment, "_prepare_data") as prepare, \
                patch.object(experiment, "_make_final_run") as fit:
            with self.assertRaisesRegex(ValueError, "this model"):
                experiment.train_one(self.config_path, "moebs_transformer", 42,
                                     "artifacts/runs/synthetic/run_manifest.json")
            manifest["effective_config"]["training"]["patience"] = 1
            with self.assertRaisesRegex(ValueError, "frozen tuning protocol"):
                experiment.train_one(self.config_path, "tft", 42,
                                     "artifacts/runs/synthetic/run_manifest.json")
            prepare.assert_not_called()
            fit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
