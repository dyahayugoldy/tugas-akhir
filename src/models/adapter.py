"""Model-neutral lifecycle, train-only fitting, immutable data binding and reload."""

import copy
import hashlib
from pathlib import Path
import random
import time

import numpy as np
import torch

from src.data.harmonize_workbooks import load_json, sha256_file
from src.features.window_dataset import WindowDataset
from .networks import INPUT_FIELDS, build_network


def configure_runtime(runtime, seed):
    if runtime["precision"] != "float32" or runtime["device"] not in ("cpu", "cuda"):
        raise ValueError("WP5 supports explicit cpu/cuda float32 only")
    if runtime["device"] == "cuda" and not torch.cuda.is_available():
        raise ValueError("Requested CUDA is unavailable; no silent device fallback")
    if type(runtime["threads"]) is not int or runtime["threads"] < 1:
        raise ValueError("Thread count must be positive")
    torch.set_num_threads(runtime["threads"])
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = runtime["deterministic"]
    torch.use_deterministic_algorithms(runtime["deterministic"])


def synchronize(device):
    if str(device).startswith("cuda"):
        torch.cuda.synchronize(device)


def descriptor(schema, preprocessing, manifest_id, manifest_sha256):
    fields = ("encoder_observed", "encoder_known", "decoder_known", "decoder_lags", "static_categorical")
    if schema["encoder_known"] != schema["decoder_known"]:
        raise ValueError("Encoder and decoder calendar contracts disagree")
    if schema["encoder_observed"] != [preprocessing["target_field"], *schema["decoder_lags"]]:
        raise ValueError("Observed channel order must be target then shared lags")
    if preprocessing["fit_split"] != "train" or preprocessing["unknown_id"] != 0:
        raise ValueError("Model requires frozen train-only preprocessing with unknown ID zero")
    return {**{key: list(schema[key]) for key in fields},
            "cardinalities": [max(preprocessing["vocabularies"][field].values()) + 1
                              for field in schema["static_categorical"]],
            "encoder_weeks": schema["shapes_excluding_sample_axis"]["encoder_observed"][0],
            "horizon_weeks": schema["shapes_excluding_sample_axis"]["y"][0],
            "target_scaler": copy.deepcopy(preprocessing["target"]), "unit": schema["y_unit"],
            "manifest_id": manifest_id, "manifest_sha256": manifest_sha256}


class ModelData(WindowDataset):
    """Only a framework boundary; preserves the existing WP4 loader and order."""
    def __init__(self, manifest_path, model, split):
        super().__init__(manifest_path, model, split)
        self.split = split
        self.contract = descriptor(self.schema, load_json(self.base / "preprocessing.json"),
                                   self.manifest_id, sha256_file(manifest_path))


def validate_architecture(name, contract, config):
    required = {"d_model", "n_heads", "dropout", "embedding_dim"}
    required |= {"lstm_layers"} if name == "tft" else {"encoder_layers", "decoder_layers", "d_ff", "position_base"}
    if set(config) != required:
        raise ValueError("Architecture config has missing or unsupported fields")
    integers = required - {"dropout", "position_base"}
    if any(type(config[key]) is not int or config[key] <= 0 for key in integers):
        raise ValueError("Architecture sizes must be positive integers")
    if config["d_model"] % config["n_heads"] or config["d_model"] % 2 or not 0 <= config["dropout"] < 1:
        raise ValueError("Invalid attention dimensions or dropout")
    if "position_base" in config and config["position_base"] <= 1:
        raise ValueError("Position base must exceed one")
    if not contract["cardinalities"] or any(size < 1 for size in contract["cardinalities"]):
        raise ValueError("Static category vocabularies are required")


class ForecastAdapter:
    def __init__(self, name, contract, architecture, *, runtime, seed):
        validate_architecture(name, contract, architecture)
        self.name, self.contract, self.architecture = name, copy.deepcopy(contract), copy.deepcopy(architecture)
        self.runtime, self.seed = copy.deepcopy(runtime), seed
        configure_runtime(runtime, seed)
        self.device = torch.device(runtime["device"])
        self.network = build_network(name, contract, architecture).to(self.device, dtype=torch.float32)
        self.fit_record = None

    def tensor_inputs(self, inputs):
        if set(inputs) != INPUT_FIELDS:
            raise ValueError("Forward accepts exactly the shared input fields; labels/metadata are forbidden")
        result = {}
        for key, value in inputs.items():
            dtype = torch.bool if "mask" in key else torch.int64 if key == "static_categorical" else torch.float32
            array = torch.as_tensor(value, device=self.device)
            if array.dtype != dtype:
                raise ValueError("Input dtype differs from the shared tensor contract")
            result[key] = array
        b = result["encoder_observed"].shape[0]
        shapes = {"encoder_observed": (b, self.contract["encoder_weeks"], len(self.contract["encoder_observed"])),
                  "encoder_known": (b, self.contract["encoder_weeks"], len(self.contract["encoder_known"])),
                  "decoder_known": (b, self.contract["horizon_weeks"], len(self.contract["decoder_known"])),
                  "decoder_lags": (b, self.contract["horizon_weeks"], len(self.contract["decoder_lags"])),
                  "static_categorical": (b, len(self.contract["cardinalities"]))}
        shapes["encoder_observed_mask"], shapes["decoder_lag_mask"] = shapes["encoder_observed"], shapes["decoder_lags"]
        if b < 1 or any(tuple(result[key].shape) != shape for key, shape in shapes.items()):
            raise ValueError("Model input shape differs from bound WP4 contract")
        for key in ("encoder_observed", "decoder_lags", "encoder_known", "decoder_known"):
            values = result[key]
            mask_key = {"encoder_observed": "encoder_observed_mask", "decoder_lags": "decoder_lag_mask"}.get(key)
            if not torch.isfinite(values[result[mask_key]] if mask_key else values).all():
                raise ValueError("Nonfinite available model input")
        for i, size in enumerate(self.contract["cardinalities"]):
            column = result["static_categorical"][:, i]
            if (column < 0).any() or (column >= size).any():
                raise ValueError("Category ID is outside the frozen training vocabulary")
        return result

    def forward(self, inputs):
        return self.network(self.tensor_inputs(inputs))

    def _check_data(self, data, expected_split=None):
        if data.contract != self.contract or (expected_split is not None and data.split != expected_split):
            raise ValueError("Data split or immutable manifest differs from the model binding")

    def predict(self, data, *, batch_size):
        self._check_data(data)
        self.network.eval()
        predictions, identities = [], []
        synchronize(self.device)
        start = time.perf_counter()
        with torch.inference_mode():
            for batch in data.batches(batch_size):
                result = self.forward(batch["inputs"])
                if not torch.isfinite(result).all():
                    raise ValueError("Model produced nonfinite forecasts")
                predictions.append(result.cpu().numpy())
                identities.extend(batch["metadata"]["sample_id"])
        synchronize(self.device)
        elapsed = time.perf_counter() - start
        scaled = np.concatenate(predictions)
        scale = self.contract["target_scaler"]
        prediction = scaled.astype(np.float64) * scale["scale"] + scale["mean"]
        if len(prediction) != len(data) or list(identities) != list(data.arrays["sample_id"]):
            raise ValueError("Prediction sample count or order changed")
        return {"sample_id": np.asarray(identities), "prediction": prediction, "scaled": scaled,
                "inference_seconds": elapsed}

    def fit(self, train_data, validation_data, run_config):
        self._check_data(train_data, "train")
        self._check_data(validation_data, "validation")
        required = {"batch_size", "max_epochs", "patience", "min_delta", "learning_rate", "weight_decay",
                    "gradient_clip", "optimizer", "loss", "shuffle", "max_seconds", "checkpoint_selection"}
        if set(run_config) != required or run_config["optimizer"] != "adamw" or run_config["loss"] != "mse" or run_config["checkpoint_selection"] != "validation_rmse":
            raise ValueError("Unsupported training protocol")
        if any(type(run_config[k]) is not int or run_config[k] < 1 for k in ("batch_size", "max_epochs", "patience")):
            raise ValueError("Invalid batch/epoch/early-stop limits")
        if any(not np.isfinite(run_config[k]) or run_config[k] <= 0 for k in ("learning_rate", "gradient_clip", "max_seconds")) or run_config["weight_decay"] < 0 or run_config["min_delta"] < 0:
            raise ValueError("Invalid training resource or optimization limits")
        configure_runtime(self.runtime, self.seed)
        optimizer = torch.optim.AdamW(self.network.parameters(), lr=run_config["learning_rate"], weight_decay=run_config["weight_decay"])
        history, best_state, best, stale = [], None, float("inf"), 0
        synchronize(self.device)
        started = time.perf_counter()
        for epoch in range(run_config["max_epochs"]):
            self.network.train()
            seen, loss_sum = 0, 0.0
            for batch in train_data.batches(run_config["batch_size"], shuffle=run_config["shuffle"], seed=self.seed + epoch):
                if time.perf_counter() - started >= run_config["max_seconds"]:
                    raise TimeoutError("Training reached its configured wall-clock cap; incomplete trial rejected")
                optimizer.zero_grad(set_to_none=True)
                prediction = self.forward(batch["inputs"])
                labels = torch.as_tensor(batch["labels"]["y_scaled"], device=self.device)
                loss = torch.nn.functional.mse_loss(prediction, labels)
                if not torch.isfinite(loss):
                    raise FloatingPointError("Nonfinite training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.network.parameters(), run_config["gradient_clip"], error_if_nonfinite=True)
                optimizer.step()
                seen += len(labels)
                loss_sum += float(loss.detach()) * len(labels)
            if seen != len(train_data):
                raise ValueError("Training dropped samples")
            result = self.predict(validation_data, batch_size=run_config["batch_size"])
            rmse = float(np.sqrt(np.square(result["prediction"] - validation_data.arrays["y"].astype(np.float64)).mean()))
            history.append({"epoch": epoch + 1, "train_samples": seen, "validation_samples": len(validation_data),
                            "train_mse_scaled": loss_sum / seen, "validation_rmse": rmse})
            if rmse < best - run_config["min_delta"]:
                best, stale = rmse, 0
                best_state = {key: value.detach().cpu().clone() for key, value in self.network.state_dict().items()}
                best_epoch = epoch + 1
            else:
                stale += 1
            if stale >= run_config["patience"]:
                break
        synchronize(self.device)
        elapsed = time.perf_counter() - started
        if elapsed > run_config["max_seconds"]:
            raise TimeoutError("Training including validation exceeded its configured wall-clock cap")
        self.network.load_state_dict(best_state)
        self.network.eval()
        self.fit_record = {"history": history, "best_epoch": best_epoch, "best_validation_rmse": best,
                           "training_seconds": elapsed, "train_samples": len(train_data),
                           "validation_samples": len(validation_data), "test_used": False,
                           "training_config": copy.deepcopy(run_config), "optimizer_state_saved": False}
        return self, copy.deepcopy(self.fit_record)

    def metadata(self):
        return {"architecture": self.name, "parameter_count": sum(p.numel() for p in self.network.parameters()),
                "effective_config": copy.deepcopy(self.architecture), "contract": copy.deepcopy(self.contract),
                "seed": self.seed, "runtime": copy.deepcopy(self.runtime), "output": "point_forecast_unclipped_pcs"}

    def save(self, path):
        # Exclusive artifact creation: never overwrite another run/checkpoint.
        path = Path(path)
        payload = {"format_version": 1, "metadata": self.metadata(), "fit_record": self.fit_record,
                   "state_dict": {key: value.detach().cpu() for key, value in self.network.state_dict().items()}}
        with path.open("xb") as stream:
            torch.save(payload, stream)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.with_suffix(path.suffix + ".sha256").open("x", encoding="ascii") as stream:
            stream.write(digest)

    @classmethod
    def load(cls, path, *, expected_contract, runtime):
        path = Path(path)
        if sha256_file(path) != path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii"):
            raise ValueError("Checkpoint checksum mismatch")
        payload = torch.load(path, map_location="cpu", weights_only=True)
        if payload["format_version"] != 1 or payload["metadata"]["contract"] != expected_contract:
            raise ValueError("Checkpoint uses a different immutable data contract")
        meta = payload["metadata"]
        model = cls(meta["architecture"], expected_contract, meta["effective_config"], runtime=runtime, seed=meta["seed"])
        model.network.load_state_dict(payload["state_dict"], strict=True)
        model.network.eval()
        model.fit_record = payload["fit_record"]
        return model
