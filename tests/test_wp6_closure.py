"""Synthetic closure tests: stopping rules, search replay and resume accounting."""

import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch

from src.data.harmonize_workbooks import ROOT, load_json
from src.models.cohort import audit_fit, audit_resume, private_path, replay_search, audit_plan, verify
from src.models import cohort, experiment
from src.data.harmonize_workbooks import sha256_file
from src.models.search import search
from src.utils.manifest import write_json
import test_models as model_fixture


def fit_record(config):
    return {"training_config": copy.deepcopy(config["training"]), "training_seconds": 8.0,
            "test_used": False, "epochs_completed": 12, "best_epoch": 2, "best_validation_rmse": 4.0,
            "training_update_seconds": 6.0, "validation_selection_seconds": 1.2,
            "history": [{"epoch": i + 1, "train_samples": 2, "validation_samples": 2,
                         "validation_rmse": 5.0 if i == 0 else 4.0, "train_mse_scaled": 1.0,
                         "training_update_seconds": 0.5, "validation_selection_seconds": 0.1}
                        for i in range(12)]}


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.config = load_json(ROOT / "configs/models.wp6.v1.json")

    def test_stopping_sequence_rejects_premature_and_after_patience(self):
        fit = fit_record(self.config)
        self.assertEqual(audit_fit(fit, self.config, 2, 2)["best_epoch"], 2)
        early = copy.deepcopy(fit)
        early["history"].pop()
        early["epochs_completed"] = 11
        with self.assertRaisesRegex(ValueError, "prematurely"):
            audit_fit(early, self.config, 2, 2)
        late = copy.deepcopy(fit)
        late["history"].append({**late["history"][-1], "epoch": 13})
        late["epochs_completed"] = 13
        with self.assertRaisesRegex(ValueError, "continued after patience"):
            audit_fit(late, self.config, 2, 2)

    def test_real_adapter_restores_best_weights_after_ten_nonimprovements(self):
        fixture = model_fixture.ModelTests()
        fixture.setUp()
        model = fixture.model("moebs_transformer")
        train, validation = model_fixture.SyntheticData(), model_fixture.SyntheticData("validation")
        training = {**fixture.training, "max_epochs": 100, "patience": 10, "min_delta": 0.0}
        weights, calls = [], []

        def predict(data, *, batch_size):
            calls.append(1)
            weights.append({k: v.detach().cpu().clone() for k, v in model.network.state_dict().items()})
            error = 2.0 if len(calls) == 2 else 4.0
            return {"prediction": data.arrays["y"].astype(float) + error}

        with patch.object(model, "predict", side_effect=predict):
            _, fit = model.fit(train, validation, training)
        self.assertEqual(fit["epochs_completed"], 12)
        self.assertEqual(fit["best_epoch"], 2)
        for key, value in model.network.state_dict().items():
            self.assertTrue(torch.equal(value.cpu(), weights[1][key]))

    def test_search_replay_rejects_different_candidate_or_winner(self):
        records = []

        def evaluate(parameters, trial_id, remaining):
            metric = {"rmse": 10.0 - trial_id, "mae": 5.0 - trial_id / 10}
            records.append({"metrics": metric, "metadata": {"parameter_count": 100}})
            return {"validation_rmse": metric["rmse"], "validation_mae": metric["mae"], "parameter_count": 100}

        outcome = search(self.config["search"], method="random", seed=self.config["tuning"]["seed"], evaluate=evaluate)
        self.assertEqual(replay_search(self.config, "tft", records, outcome), 3)
        changed = copy.deepcopy(outcome)
        changed["trials"][0]["parameters"]["learning_rate"] = 99.0
        with self.assertRaisesRegex(ValueError, "candidate parameters"):
            replay_search(self.config, "tft", records, changed)
        changed = copy.deepcopy(outcome)
        changed["selected_trial_id"] = 0
        with self.assertRaisesRegex(ValueError, "selection differs"):
            replay_search(self.config, "tft", records, changed)

    def test_resume_checks_copied_bytes_and_accounts_old_session_and_repeated_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            original, resumed = Path(folder) / "old", Path(folder) / "resumed"
            (original / "source/configs").mkdir(parents=True)
            write_json(original / "source/configs/models.wp6.v1.json", self.config)
            write_json(original / "search.json", {"elapsed_seconds": 50.0, "selected_trial_id": 1,
                       "trials": [{"elapsed_seconds": 7.0} for _ in range(4)]})
            resumed.mkdir()
            write_json(resumed / "search.json", {"elapsed_seconds": 10.0, "selected_trial_id": 1,
                       "trials": [{"elapsed_seconds": 0.001} for _ in range(4)]})
            for i in range(4):
                old, new = original / "trials" / f"trial-{i:03d}", resumed / "trials" / f"trial-{i:03d}"
                old.mkdir(parents=True)
                new.mkdir(parents=True)
                write_json(old / "training.json", {"architecture": {}, "training": self.config["training"],
                           "metadata": {}, "metrics": {}, "fit": fit_record(self.config)})
                (old / "checkpoint.pt").write_bytes(b"synthetic-checkpoint")
                (old / "checkpoint.pt.sha256").write_bytes(b"synthetic-checksum")
                np.savez(old / "validation_predictions.npz", prediction=np.array([1., 2.]), sample_id=np.array(["a", "b"]))
                for p in old.iterdir():
                    shutil.copyfile(p, new / p.name)
            spec = {"copied_trial_ids": [0, 1, 2], "recomputed_trial_ids": [3]}
            with patch("src.models.cohort.audit_predictions"):
                audited = audit_resume(original, resumed, self.config, [1, 2], [1, 2], spec)
                self.assertEqual(audited["reconciled_selected_search_seconds"], 31.0)
                self.assertEqual(audited["original_plus_resumed_search_seconds"], 60.0)
                self.assertEqual(audited["completed_physical_fits"], 5)
                self.assertEqual(audited["recomputed_extra_fit_seconds"], 8.0)
                (resumed / "trials/trial-001/checkpoint.pt").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "Copied trial bytes"):
                    audit_resume(original, resumed, self.config, [1, 2], [1, 2], spec)

    def test_private_path_rejects_traversal_and_public_paths(self):
        for name in ("../outside.json", "reports/audit.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                private_path(ROOT, name)

    def test_missing_members_and_config_tampering_rejected_before_data_access(self):
        plan = {"training_config": "configs/models.wp6.v1.json",
                "training_config_sha256": sha256_file(ROOT / "configs/models.wp6.v1.json"),
                "tuning_runs": [], "final_runs": []}
        with patch("src.models.cohort.ModelData") as data:
            with self.assertRaisesRegex(ValueError, "Incomplete cohort grid"):
                audit_plan(plan)
            plan["training_config_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "training config changed"):
                audit_plan(plan)
            data.assert_not_called()

    def test_standard_cohort_command_routes_to_closure_verifier(self):
        with patch.object(experiment, "load_json", return_value={"stage": "WP6_certified_cohort"}), \
                patch.object(cohort, "verify", return_value={"status": "passed"}) as check:
            result = experiment.verify_cohort(ROOT / "artifacts/runs/synthetic/cohort.json")
            self.assertEqual(result, {"status": "passed"})
            check.assert_called_once()

    def test_closure_evidence_tampering_rejected_before_reaudit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            base = root / "artifacts/runs/closure"
            base.mkdir(parents=True)
            pp, ap = base / "plan.json", base / "audit.json"
            plan = {"tuning_runs": [], "final_runs": []}
            result = {"status": "synthetic"}
            write_json(pp, plan)
            write_json(ap, result)
            verifier_path = base / "verifier_source.py"
            verifier_path.write_bytes(Path(cohort.__file__).read_bytes())
            receipt = {"stage": "WP6_certified_cohort", "purpose": experiment.PURPOSE,
                       "status": "synthetic", "test_predictions": 0,
                       "plan_path": pp.relative_to(root).as_posix(), "plan_sha256": sha256_file(pp),
                       "audit_path": ap.relative_to(root).as_posix(), "audit_sha256": sha256_file(ap),
                       "verifier_sha256": sha256_file(Path(cohort.__file__)),
                       "verifier_snapshot_path": verifier_path.relative_to(root).as_posix(), **plan}
            path = base / "cohort.json"
            write_json(path, receipt)
            with patch.object(cohort, "audit_plan", return_value=result) as reaudit:
                self.assertEqual(verify(path, root=root), receipt)
                ap.write_bytes(b"synthetic corruption")
                with self.assertRaisesRegex(ValueError, "Closure evidence checksum"):
                    verify(path, root=root)
                self.assertEqual(reaudit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
