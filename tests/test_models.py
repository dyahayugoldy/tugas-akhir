"""WP5 architecture, causal counterfactual, lifecycle and optimizer tests.

Synthetic inputs only; business data is never opened by these unit tests.
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

from src.data.harmonize_workbooks import ROOT
from src.evaluation.metrics import point_metrics
from src.models.adapter import ForecastAdapter
from src.models.search import dominates, search, update_archive, validate_search


MODELS = ("moebs_transformer", "nspace_transformer", "tft")


class SyntheticData:
    def __init__(self, split="train", horizon=3):
        self.split = split
        self.contract = {"encoder_observed": ["quantity", "lag_1", "lag_2"],
                         "encoder_known": ["calendar_a", "calendar_b"],
                         "decoder_known": ["calendar_a", "calendar_b"], "decoder_lags": ["lag_1", "lag_2"],
                         "static_categorical": ["country", "brand"], "cardinalities": [3, 4],
                         "encoder_weeks": 4, "horizon_weeks": horizon,
                         "target_scaler": {"mean": 2.5, "scale": 3.0}, "unit": "pcs",
                         "manifest_id": "synthetic", "manifest_sha256": "a" * 64}
        rng, n = np.random.default_rng(7 if split == "train" else 8), 5
        f = lambda shape: rng.normal(size=shape).astype(np.float32)
        self.arrays = {"encoder_observed": f((n, 4, 3)), "encoder_observed_mask": np.ones((n, 4, 3), bool),
                       "encoder_known": f((n, 4, 2)), "decoder_known": f((n, horizon, 2)),
                       "decoder_lags": f((n, horizon, 2)), "decoder_lag_mask": np.ones((n, horizon, 2), bool),
                       "static_categorical": np.array([[0, 0], [1, 2], [2, 3], [0, 1], [1, 0]], np.int64),
                       "sample_id": np.array([f"{split}-{i}" for i in range(n)]),
                       "series_id": np.array([f"series-{i}" for i in range(n)]),
                       "origin_time": np.array(["2024-01-01"] * n), "y_scaled": f((n, horizon))}
        self.arrays["y"] = self.arrays["y_scaled"] * 3 + 2.5
        self.arrays["decoder_lag_mask"][:, 1:, 0] = False
        self.arrays["encoder_observed_mask"][:, 0, 1] = False

    def __len__(self):
        return len(self.arrays["sample_id"])

    def batches(self, batch_size, *, shuffle=False, seed=None):
        order = np.arange(len(self))
        if shuffle:
            np.random.default_rng(seed).shuffle(order)
        labels, metadata = {"y", "y_scaled"}, {"sample_id", "series_id", "origin_time"}
        for offset in range(0, len(self), batch_size):
            idx = order[offset:offset + batch_size]
            yield {"inputs": {k: v[idx] for k, v in self.arrays.items() if k not in labels | metadata},
                   "labels": {k: self.arrays[k][idx] for k in labels}, "metadata": {k: self.arrays[k][idx] for k in metadata}}


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "configs/models.wp5.v1.json").read_text())
        self.training = copy.deepcopy(self.config["training"])
        self.training.update(batch_size=2, max_seconds=60)
        self.data = SyntheticData()

    def model(self, name, *, seed=42, data=None):
        architecture = copy.deepcopy(self.config["models"][name])
        architecture.update(d_model=8, dropout=0.0, embedding_dim=2)
        if name != "tft":
            architecture.update(d_ff=16, encoder_layers=1, decoder_layers=1)
        return ForecastAdapter(name, (data or self.data).contract, architecture, runtime=self.config["runtime"], seed=seed)

    def test_three_forward_shapes_float32_gradients_and_real_architecture_components(self):
        batch = next(self.data.batches(2))
        for name in MODELS:
            with self.subTest(model=name):
                model = self.model(name)
                prediction = model.forward(batch["inputs"])
                self.assertEqual(prediction.shape, (2, 3))
                self.assertEqual(prediction.dtype, torch.float32)
                loss = (prediction - torch.as_tensor(batch["labels"]["y_scaled"])).square().mean()
                loss.backward()
                self.assertTrue(all(torch.isfinite(p.grad).all() for p in model.network.parameters() if p.grad is not None))
                self.assertGreater(float(model.network.static.tables[0].weight.grad[0].abs().sum()), 0)
                self.assertIsNone(model.network.static.tables[0].padding_idx)
                if name == "nspace_transformer":
                    self.assertIsNotNone(model.network.nspace_projection.weight.grad)
                    self.assertIsNone(model.network.nspace_projection.bias)
                if name == "tft":
                    for module in ("static_selection", "encoder_selection", "decoder_selection", "encoder_lstm", "decoder_lstm", "enrich", "attention", "final_gate"):
                        self.assertTrue(hasattr(model.network, module))

    def test_masked_values_and_labels_cannot_change_prediction(self):
        batch = next(self.data.batches(2))
        changed = copy.deepcopy(batch["inputs"])
        changed["encoder_observed"][~changed["encoder_observed_mask"]] = np.nan
        changed["decoder_lags"][~changed["decoder_lag_mask"]] = 123456
        for name in MODELS:
            with self.subTest(model=name):
                model = self.model(name)
                model.network.eval()
                with torch.no_grad():
                    before, after = model.forward(batch["inputs"]), model.forward(changed)
                torch.testing.assert_close(before, after, rtol=0, atol=0)
                with self.assertRaisesRegex(ValueError, "labels/metadata"):
                    model.forward({**batch["inputs"], "y": batch["labels"]["y"]})
                before = model.predict(self.data, batch_size=2)["prediction"]
                self.data.arrays["y"][:] = 987654
                self.data.arrays["y_scaled"][:] = -987654
                np.testing.assert_array_equal(before, model.predict(self.data, batch_size=2)["prediction"])

    def test_multistep_future_decoder_context_cannot_change_earlier_horizon(self):
        batch = next(self.data.batches(2))["inputs"]
        changed = copy.deepcopy(batch)
        changed["decoder_known"][:, 1:] += 1000
        changed["decoder_lags"][:, 1:] += 1000
        for name in MODELS:
            model = self.model(name)
            model.network.eval()
            with torch.no_grad():
                before, after = model.forward(batch), model.forward(changed)
            torch.testing.assert_close(before[:, 0], after[:, 0], rtol=0, atol=0)
            self.assertFalse(torch.equal(before[:, -1], after[:, -1]))

    def test_tft_interpretation_weights_and_causal_attention(self):
        model = self.model("tft")
        model.network.eval()
        _, weights = model.network(model.tensor_inputs(next(self.data.batches(2))["inputs"]), return_interpretation=True)
        for key in ("static_weights", "encoder_weights", "decoder_weights", "temporal_attention"):
            torch.testing.assert_close(weights[key].sum(-1), torch.ones_like(weights[key].sum(-1)))
        attention = weights["temporal_attention"]
        self.assertEqual(tuple(attention.shape), (2, 3, 7))
        self.assertEqual(float(attention[:, 0, 5:].detach().abs().sum()), 0)
        self.assertEqual(model.network.attention.value.out_features, 4)

    def test_full_epoch_no_dropped_samples_seed_reproducibility_reload_and_tamper(self):
        for name in MODELS:
            with self.subTest(model=name), tempfile.TemporaryDirectory() as directory:
                train, validation = SyntheticData(), SyntheticData("validation")
                model = self.model(name)
                before = {k: v.clone() for k, v in model.network.state_dict().items()}
                _, record = model.fit(train, validation, self.training)
                self.assertEqual(record["history"][0]["train_samples"], 5)
                self.assertFalse(record["test_used"])
                self.assertTrue(any(not torch.equal(v, before[k]) for k, v in model.network.state_dict().items()))
                prediction = model.predict(validation, batch_size=2)
                self.assertEqual(list(prediction["sample_id"]), list(validation.arrays["sample_id"]))
                self.assertEqual(prediction["prediction"].shape, (5, 3))
                checkpoint = Path(directory) / "checkpoint.pt"
                model.save(checkpoint)
                reloaded = ForecastAdapter.load(checkpoint, expected_contract=train.contract, runtime=self.config["runtime"])
                np.testing.assert_array_equal(prediction["prediction"], reloaded.predict(validation, batch_size=2)["prediction"])
                duplicate = self.model(name)
                duplicate.fit(train, validation, self.training)
                np.testing.assert_array_equal(prediction["prediction"], duplicate.predict(validation, batch_size=2)["prediction"])
                with self.assertRaises(FileExistsError):
                    model.save(checkpoint)
                mismatch = copy.deepcopy(train.contract)
                mismatch["manifest_id"] = "different"
                with self.assertRaisesRegex(ValueError, "contract"):
                    ForecastAdapter.load(checkpoint, expected_contract=mismatch, runtime=self.config["runtime"])
                checkpoint.write_bytes(b"synthetic corruption")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    ForecastAdapter.load(checkpoint, expected_contract=train.contract, runtime=self.config["runtime"])

    def test_fit_rejects_holdout_wrong_binding_invalid_inputs_and_limits(self):
        model = self.model("moebs_transformer")
        with self.assertRaisesRegex(ValueError, "split"):
            model.fit(SyntheticData("test"), SyntheticData("validation"), self.training)
        wrong = SyntheticData("validation")
        wrong.contract["manifest_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "manifest"):
            model.fit(self.data, wrong, self.training)
        inputs = next(self.data.batches(2))["inputs"]
        inputs["static_categorical"][0, 0] = 99
        with self.assertRaisesRegex(ValueError, "Category"):
            model.forward(inputs)
        bad = copy.deepcopy(self.training)
        bad["max_seconds"] = 0.000000001
        with self.assertRaises(TimeoutError):
            model.fit(self.data, SyntheticData("validation"), bad)

    def test_horizon_one_and_final_batch_size_one(self):
        data = SyntheticData(horizon=1)
        for name in MODELS:
            model = self.model(name, data=data)
            self.assertEqual(model.predict(data, batch_size=2)["prediction"].shape, (5, 1))


class SearchAndMetricTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "configs/models.wp5.v1.json").read_text())

    def test_known_metric_values_zeros_epsilon_and_constant_r2(self):
        metrics = point_metrics([0, 2, 4], [0, 1, 6], smape_epsilon=1e-6)
        self.assertEqual(metrics["mae"], 1)
        self.assertAlmostEqual(metrics["rmse"], (5 / 3) ** 0.5)
        self.assertEqual(metrics["mape"], 50)
        self.assertEqual(metrics["mape_count"], 2)
        self.assertAlmostEqual(metrics["mape_coverage"], 2 / 3)
        self.assertAlmostEqual(metrics["smape"], (200 / 3 + 40) / 3)
        self.assertAlmostEqual(metrics["r2"], 0.375)
        zero = point_metrics([0, 0], [0, 0], smape_epsilon=1e-6)
        self.assertIsNone(zero["r2"])
        self.assertIsNone(zero["mape"])
        self.assertEqual(zero["smape"], 0)
        self.assertEqual(zero["mape_count"], 0)
        with self.assertRaises(ValueError):
            point_metrics([1], [float("nan")], smape_epsilon=1e-6)

    def test_moebs_maneuvers_budgets_pareto_and_reproducibility(self):
        config = copy.deepcopy(self.config["search"])
        config["max_evaluations"] = 12
        config["moebs"]["max_iterations"] = 5
        def evaluator(parameters, trial, remaining):
            self.assertGreater(remaining, 0)
            x = parameters["learning_rate"]
            return {"validation_rmse": x, "validation_mae": 1 / x, "parameter_count": parameters["d_model"]}
        a = search(config, method="moebs", seed=42, evaluate=evaluator)
        b = search(config, method="moebs", seed=42, evaluate=evaluator)
        self.assertEqual(a["evaluations"], 12)
        self.assertEqual(a["stop_reason"], "max_evaluations")
        self.assertEqual([e["parameters"] for e in a["trials"]], [e["parameters"] for e in b["trials"]])
        self.assertIn("predator", [e["maneuver"] for e in a["trials"]])
        self.assertTrue(any(e["maneuver"] in ("vertical", "horizontal") for e in a["trials"]))
        for i in a["pareto_trial_ids"]:
            self.assertFalse(any(dominates(e["objectives"], a["trials"][i]["objectives"]) for e in a["trials"]))
        self.assertFalse(a["test_used"])
        random = search(config, method="random", seed=42, evaluate=evaluator)
        self.assertEqual(random["evaluations"], 12)

    def test_archive_capacity_dominance_and_failure_accounting(self):
        rng = np.random.default_rng(2)
        archive = []
        for i in range(10):
            archive = update_archive(archive, {"objectives": [i, 10 - i], "trial_id": i}, 3, 2, rng)
        self.assertEqual(len(archive), 3)
        archive = update_archive(archive, {"objectives": [-1, -1], "trial_id": 11}, 3, 2, rng)
        self.assertEqual([e["trial_id"] for e in archive], [11])
        def evaluate(parameters, trial, remaining):
            if trial == 0:
                raise TimeoutError()
            return {"validation_rmse": trial, "validation_mae": trial, "parameter_count": 5}
        result = search(self.config["search"], method="random", seed=42, evaluate=evaluate)
        self.assertEqual(result["evaluations"], 4)
        self.assertEqual(result["trials"][0]["status"], "failed")
        self.assertEqual(result["selected_trial_id"], 1)
        bad = copy.deepcopy(self.config["search"])
        bad["objective_names"][0] = "test_rmse"
        with self.assertRaisesRegex(ValueError, "validation"):
            validate_search(bad)


if __name__ == "__main__":
    unittest.main()
