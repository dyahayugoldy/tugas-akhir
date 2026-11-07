"""One NumPy batch contract for all three WP5 model consumers.

Inputs and labels are separate dictionaries. No adapter may re-fit transforms
or drop samples. This is a data loader contract, not model implementation.
"""

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.harmonize_workbooks import ROOT, load_json, relative_path, sha256_file


class WindowDataset:
    def __init__(self, manifest_path, model, split, *, manifest=None):
        self.base = Path(manifest_path).resolve().parent
        self.manifest = load_json(manifest_path) if manifest is None else manifest
        if manifest is None and not self.manifest.get("ready_for_models"):
            raise ValueError("WP4 manifest has not been sealed for model consumers")
        if self.manifest.get("stage") != "WP4_split_windows" or model not in self.manifest["shared_models"]:
            raise ValueError("Unknown model consumer or split manifest")
        if split not in ("train", "validation", "test"):
            raise ValueError("Unknown split")
        self.manifest_id = self.manifest["manifest_id"]
        for name in (f"tensors/{split}.npz", "preprocessing.json", "tensor_schema.json", "index/window_index.csv.gz"):
            if sha256_file(self.base / name) != self.manifest["outputs"][name]:
                raise ValueError("Immutable WP4 artifact checksum mismatch")
        self.schema = load_json(self.base / "tensor_schema.json")
        with np.load(self.base / f"tensors/{split}.npz", allow_pickle=False) as saved:
            self.arrays = {key: saved[key] for key in saved.files}
        if len(self) != self.manifest["counts"][split]["samples"]:
            raise ValueError("Shared sample count differs from the split manifest")
        if any(len(value) != len(self) for value in self.arrays.values()):
            raise ValueError("Inconsistent tensor sample axes")
        index = pd.read_csv(self.base / "index/window_index.csv.gz", dtype=str, keep_default_na=False)
        rows = index.loc[index.split.eq(split)]
        for key in ("sample_id", "series_id", "origin_time"):
            if not np.array_equal(self.arrays[key], rows[key].to_numpy()):
                raise ValueError("Tensor order differs from the immutable sample index")
        shapes = self.schema["shapes_excluding_sample_axis"]
        if set(shapes) != set(self.arrays):
            raise ValueError("Tensor fields differ from the shared schema")
        for key, value in self.arrays.items():
            if list(value.shape[1:]) != shapes[key]:
                raise ValueError("Tensor shape differs from the shared schema")
            if key in ("sample_id", "series_id", "origin_time"):
                continue
            dtype = np.bool_ if "mask" in key else np.int64 if key == "static_categorical" else np.float32
            if value.dtype != dtype or not np.isfinite(value).all():
                raise ValueError("Invalid shared tensor dtype or nonfinite value")

    def __len__(self):
        return len(self.arrays["sample_id"])

    def batches(self, batch_size=256, *, shuffle=False, seed=None):
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("Batch size must be a positive integer")
        if shuffle and seed is None:
            raise ValueError("Shuffling requires an explicit seed")
        order = np.arange(len(self))
        if shuffle:
            np.random.default_rng(seed).shuffle(order)
        labels = set(self.schema["label_fields"])
        metadata = {"sample_id", "series_id", "origin_time"}
        for offset in range(0, len(self), batch_size):
            chosen = order[offset:offset + batch_size]
            yield {"inputs": {key: value[chosen] for key, value in self.arrays.items() if key not in labels | metadata},
                   "labels": {key: self.arrays[key][chosen] for key in labels},
                   "metadata": {key: self.arrays[key][chosen] for key in metadata}}


def verify_consumers(output, manifest):
    result = {"scope": "common_numpy_data_contract_framework_adapters_deferred_to_WP5", "splits": {}}
    config = load_json(output / "config.resolved.json")
    for split, count in manifest["counts"].items():
        signatures = {}
        for model in manifest["shared_models"]:
            dataset = WindowDataset(output / "split_manifest.json", model, split, manifest=manifest)
            digest, seen = hashlib.sha256(), 0
            for batch in dataset.batches(config["loader"]["batch_size"]):
                seen += len(batch["metadata"]["sample_id"])
                for group, fields in sorted(batch.items()):
                    for name, value in sorted(fields.items()):
                        digest.update((group + "/" + name).encode())
                        digest.update(value.tobytes())
            if seen != count["samples"]:
                raise ValueError("A model consumer lost samples")
            signatures[model] = {"samples": seen, "ordered_batch_sha256": digest.hexdigest()}
        if len({item["ordered_batch_sha256"] for item in signatures.values()}) != 1:
            raise ValueError("Model consumers received different samples or tensors")
        result["splits"][split] = signatures
    result["all_equal"] = True
    return result


def validate_run(manifest_path, *, root=ROOT, verify_inputs=False):
    """Verify an existing sealed run without rewriting any artifact."""
    manifest_path = Path(manifest_path).resolve()
    manifest = load_json(manifest_path)
    if manifest.get("stage") != "WP4_split_windows" or not manifest.get("ready_for_models"):
        raise ValueError("WP4 run is not ready for models")
    base = manifest_path.parent
    for name, expected in manifest["outputs"].items():
        path = (base / name).resolve()
        if not path.is_relative_to(base) or sha256_file(path) != expected:
            raise ValueError("Sealed WP4 output checksum mismatch")
    if verify_inputs:
        config = load_json(base / "config.resolved.json")
        for name, expected in manifest["inputs_sha256"].items():
            if name == config["current_data_pointer"]:
                continue  # Activation updates the pointer; immutable snapshot is checked above.
            path = relative_path(root, name, description="WP4 ancestor")
            if sha256_file(path) != expected:
                raise ValueError("WP4 ancestor checksum mismatch")
    result = verify_consumers(base, manifest)
    if result != load_json(base / "loader_verification.json"):
        raise ValueError("Recorded consumer verification differs from current artifacts")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verify-inputs", action="store_true")
    args = parser.parse_args()
    validate_run(args.manifest, verify_inputs=args.verify_inputs)
    print("WP4 checksums, sample index, tensor schema, and all three data consumers verified.")


if __name__ == "__main__":
    main()
