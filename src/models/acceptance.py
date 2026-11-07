"""Run WP5 one-epoch acceptance on the frozen shared WP4 train/validation.

Test tensors are checked by the upstream integrity validator but are never
passed to model fitting, hyperparameter selection, or prediction in WP5.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

import numpy as np
import pandas as pd
import torch

from src.data.harmonize_workbooks import ROOT, load_json, relative_path, sha256_file
from src.evaluation.metrics import point_metrics
from src.features.window_dataset import validate_run
from src.utils.manifest import collect_environment, source_info, validate_manifest, write_json
from .adapter import ForecastAdapter, ModelData, validate_architecture
from .search import search, validate_search


MODELS = ("moebs_transformer", "nspace_transformer", "tft")


def validate_config(config):
    if set(config) != {"config_version", "purpose", "current_data_pointer", "output_root", "seed", "runtime", "training", "metrics", "models", "search", "wp6_recommendation", "adaptations"}:
        raise ValueError("Missing or unsupported WP5 config fields")
    if config["purpose"] != "WP5_acceptance_not_final_comparison" or config["training"]["max_epochs"] != 1:
        raise ValueError("WP5 runner is restricted to one-epoch acceptance")
    if type(config["seed"]) is not int or config["seed"] < 0 or set(config["models"]) != set(MODELS):
        raise ValueError("One explicit seed and all three architectures are required")
    metrics = config["metrics"]
    if set(metrics) != {"smape_epsilon", "constant_r2", "mape"} or metrics["smape_epsilon"] <= 0 or metrics["constant_r2"] != "null" or metrics["mape"] != "actual_nonzero_only":
        raise ValueError("Invalid zero-aware metric policy")
    validate_search(config["search"])
    if config["runtime"]["device"] != "cpu":
        # CUDA can be used through the adapter after resource calibration in WP6.
        raise ValueError("WP5 acceptance config is calibrated for CPU only")


def consumer_digest(data, batch_size):
    digest = hashlib.sha256()
    for batch in data.batches(batch_size):
        for group, fields in sorted(batch.items()):
            for name, value in sorted(fields.items()):
                digest.update((group + "/" + name).encode())
                digest.update(value.tobytes())
    return digest.hexdigest()


def environment(runtime):
    result = collect_environment()
    result.update(framework="pytorch", framework_version=str(torch.__version__), device=runtime["device"])
    result["hardware"]["gpu"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    return result


def run(config_path, *, root=ROOT):
    root, config_path = Path(root).resolve(), Path(config_path).resolve()
    config = load_json(config_path)
    validate_config(config)
    pointer_path = relative_path(root, config["current_data_pointer"], description="WP5 pointer")
    pointer = load_json(pointer_path)
    split_path = relative_path(root, pointer["active_split_manifest"], description="WP5 split manifest")
    validate_run(split_path, root=root, verify_inputs=True)
    split_hash = sha256_file(split_path)
    output_root = relative_path(root, config["output_root"], description="WP5 output")
    if output_root != root / "artifacts/runs":
        raise ValueError("Acceptance outputs must remain in private artifacts/runs")
    source_paths = sorted((root / "src").rglob("*.py")) + [config_path, root / "requirements.txt",
                    root / "configs/run-manifest.schema.json", root / config["adaptations"]]
    sources = {path.relative_to(root).as_posix(): sha256_file(path) for path in source_paths}
    manifests, shared_digests, search_budgets = [], {}, []
    for name in MODELS:
        train, validation = (ModelData(split_path, name, part) for part in ("train", "validation"))
        for part, data in (("train", train), ("validation", validation)):
            digest = consumer_digest(data, config["training"]["batch_size"])
            if part in shared_digests and shared_digests[part] != digest:
                raise ValueError("Framework adapters received different shared samples")
            shared_digests[part] = digest
        validate_architecture(name, train.contract, config["models"][name])
        run_id = f"{name}_{config['config_version']}_seed-{config['seed']}_{datetime.now(timezone.utc):%Y%m%dt%H%M%S%fz}_{uuid.uuid4().hex[:12]}"
        base = output_root / run_id
        base.mkdir(parents=True, exist_ok=False)
        (base / "trials").mkdir()
        effective = copy.deepcopy(config)
        effective.update(model_name=name, split_manifest_path=split_path.relative_to(root).as_posix(),
                         split_manifest_sha256=split_hash, data_contract=train.contract,
                         selected_architecture=None, selected_training=None)
        write_json(base / "current-data.snapshot.json", pointer)
        write_json(base / "split_manifest.snapshot.json", train.manifest)
        write_json(base / "data-contract.json", train.contract)
        for source_name, expected in sources.items():
            destination = base / "source" / source_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as target:
                target.write((root / source_name).read_bytes())
            if sha256_file(destination) != expected:
                raise ValueError("Source changed during WP5 snapshot")
        trial_records = {}

        def evaluate(parameters, trial_id, remaining):
            architecture, training = copy.deepcopy(config["models"][name]), copy.deepcopy(config["training"])
            for key, value in parameters.items():
                (architecture if key in architecture else training)[key] = value
            training["max_seconds"] = min(training["max_seconds"], remaining)
            model = ForecastAdapter(name, train.contract, architecture, runtime=config["runtime"], seed=config["seed"])
            _, fitted = model.fit(train, validation, training)
            prediction = model.predict(validation, batch_size=training["batch_size"])
            metrics = point_metrics(validation.arrays["y"], prediction["prediction"], smape_epsilon=config["metrics"]["smape_epsilon"])
            trial_dir = base / "trials" / f"trial-{trial_id:03d}"
            trial_dir.mkdir(exist_ok=False)
            model.save(trial_dir / "checkpoint.pt")
            with (trial_dir / "validation_predictions.npz").open("xb") as stream:
                np.savez_compressed(stream, prediction=prediction["prediction"], sample_id=prediction["sample_id"])
            record = {"architecture": architecture, "training": training, "fit": fitted,
                      "metadata": model.metadata(), "metrics": metrics,
                      "inference_seconds": prediction["inference_seconds"]}
            write_json(trial_dir / "training.json", record)
            trial_records[trial_id] = record
            return {"validation_rmse": metrics["rmse"], "validation_mae": metrics["mae"],
                    "parameter_count": record["metadata"]["parameter_count"]}

        try:
            outcome = search(config["search"], method="moebs" if name == "moebs_transformer" else "random",
                             seed=config["seed"], evaluate=evaluate,
                             on_record=lambda item: write_json(base / "trials" / f"evaluation-{item['trial_id']:03d}.json", item))
            write_json(base / "search.json", outcome)
            selected = outcome["selected_trial_id"]
            selected_dir = base / "trials" / f"trial-{selected:03d}"
            trained = trial_records[selected]
            model = ForecastAdapter.load(selected_dir / "checkpoint.pt", expected_contract=train.contract, runtime=config["runtime"])
            prediction = model.predict(validation, batch_size=config["training"]["batch_size"])
            with np.load(selected_dir / "validation_predictions.npz", allow_pickle=False) as saved:
                if not np.array_equal(saved["prediction"], prediction["prediction"]) or not np.array_equal(saved["sample_id"], prediction["sample_id"]):
                    raise ValueError("Checkpoint reload changed predictions or sample order")
            model.save(base / "checkpoint.pt")
            metrics = point_metrics(validation.arrays["y"], prediction["prediction"], smape_epsilon=config["metrics"]["smape_epsilon"])
            write_json(base / "metrics.json", {"split": "validation", "purpose": config["purpose"], "global": metrics,
                       "per_horizon": {str(h + 1): point_metrics(validation.arrays["y"][:, h], prediction["prediction"][:, h],
                                         smape_epsilon=config["metrics"]["smape_epsilon"])
                                       for h in range(train.contract["horizon_weeks"])}})
            rows = []
            for i, sample in enumerate(prediction["sample_id"]):
                origin = str(validation.arrays["origin_time"][i])
                for h in range(train.contract["horizon_weeks"]):
                    rows.append({"run_id": run_id, "model_name": name, "seed": config["seed"], "split": "validation",
                                 "sample_id": sample, "series_id": validation.arrays["series_id"][i], "origin_time": origin,
                                 "target_time": (pd.Timestamp(origin) + pd.Timedelta(weeks=h)).strftime("%Y-%m-%d"),
                                 "actual": float(validation.arrays["y"][i, h]), "prediction": float(prediction["prediction"][i, h]),
                                 "target_unit": train.contract["unit"], "horizon_step": h + 1})
            with (base / "predictions.csv").open("x", encoding="utf-8", newline="") as stream:
                pd.DataFrame(rows).to_csv(stream, index=False)
            effective["selected_architecture"], effective["selected_training"] = trained["architecture"], trained["training"]
            write_json(base / "config.resolved.json", effective)
            verification = {"forward_finite": True, "one_full_epoch_each_completed_trial": all(len(r["fit"]["history"]) == 1 and r["fit"]["history"][0]["train_samples"] == len(train) for r in trial_records.values()),
                            "checkpoint_reload_exact": True, "prediction_order_exact": True,
                            "validation_predictions": len(prediction["prediction"]), "test_predictions": 0,
                            "shared_numpy_digest": dict(shared_digests), "split_manifest_sha256": split_hash,
                            "test_used_for_fitting_or_selection": False}
            if not verification["one_full_epoch_each_completed_trial"]:
                raise ValueError("One-epoch acceptance failed")
            write_json(base / "verification.json", verification)
            revision, dirty = source_info(root)
            base_rel = base.relative_to(root).as_posix()
            manifest = {"schema_version": "1", "run_type": "acceptance", "run_id": run_id, "model_name": name,
                        "config_version": config["config_version"], "seed": config["seed"],
                        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "data_manifest_id": train.manifest["input_manifest_id"],
                        "split_manifest_id": train.manifest_id, "feature_contract_version": train.manifest["feature_contract_version"],
                        "source_revision": revision, "source_dirty": dirty, "effective_config": effective,
                        "environment": environment(config["runtime"]),
                        "timing": {"training_seconds": trained["fit"]["training_seconds"], "inference_seconds": prediction["inference_seconds"]},
                        "artifacts": {"effective_config": f"{base_rel}/config.resolved.json", "checkpoint": f"{base_rel}/checkpoint.pt",
                                      "predictions": f"{base_rel}/predictions.csv", "metrics": f"{base_rel}/metrics.json", "dummy_output": None},
                        "wp5": {"purpose": config["purpose"], "architecture": model.metadata(), "verification": verification,
                                "search_seconds": outcome["elapsed_seconds"], "total_trial_training_seconds": sum(r["fit"]["training_seconds"] for r in trial_records.values()),
                                "search_evaluations": outcome["evaluations"], "selected_trial_id": selected,
                                "source_code_sha256": sources, "split_manifest_path": effective["split_manifest_path"],
                                "split_manifest_sha256": split_hash, "privacy": "private_no_raw_business_identifiers_in_logs",
                                "availability_assumption": train.manifest["availability_assumption"],
                                "coverage_assumption": train.manifest["coverage_assumption"],
                                "checksums": {p.relative_to(base).as_posix(): sha256_file(p) for p in sorted(base.rglob("*")) if p.is_file()}}}
            if sha256_file(split_path) != split_hash or any(sha256_file(root / name_) != expected for name_, expected in sources.items()):
                raise ValueError("Pinned split or source changed during acceptance")
            validate_manifest(manifest, root=root, require_files=True)
            manifest_path = base / "run_manifest.json"
            write_json(manifest_path, manifest)
            verify_acceptance(manifest_path, root=root)
            manifests.append(manifest_path)
            search_budgets.append(outcome["evaluations"])
            print(f"WP5 {name}: full epoch, {outcome['evaluations']} trials, exact reload, {len(validation)} ordered validation predictions verified.", flush=True)
        except Exception as error:
            write_json(base / "failure.json", {"status": "failed", "error_type": type(error).__name__, "test_used": False})
            raise
    if len(set(search_budgets)) != 1:
        raise ValueError("Acceptance search counts differ; completed runs retained, no fairness claim permitted")
    cohort = {"stage": "WP5_acceptance_cohort", "config_version": config["config_version"],
              "seed": config["seed"], "split_manifest_id": train.manifest_id,
              "split_manifest_sha256": split_hash, "shared_digests": shared_digests,
              "matched_evaluations": search_budgets[0], "test_predictions": 0,
              "runs": [{"model_name": load_json(path)["model_name"],
                        "manifest_path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)} for path in manifests]}
    cohort_path = output_root / f"wp5-cohort_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}.json"
    write_json(cohort_path, cohort)
    print(f"WP5 matched three-model cohort: {cohort_path.relative_to(root).as_posix()}", flush=True)
    return manifests


def verify_acceptance(manifest_path, *, root=ROOT):
    manifest_path = Path(manifest_path).resolve()
    manifest = load_json(manifest_path)
    validate_manifest(manifest, root=root, require_files=True)
    if manifest["run_type"] != "acceptance":
        raise ValueError("Not a WP5 acceptance run")
    base = manifest_path.parent
    for name, expected in manifest["wp5"]["checksums"].items():
        path = (base / name).resolve()
        if not path.is_relative_to(base) or sha256_file(path) != expected:
            raise ValueError("Acceptance artifact checksum mismatch")
    split_path = relative_path(root, manifest["wp5"]["split_manifest_path"], description="Acceptance data")
    if sha256_file(split_path) != manifest["wp5"]["split_manifest_sha256"]:
        raise ValueError("Bound split manifest changed")
    verification = manifest["wp5"]["verification"]
    if not all(verification[key] for key in ("forward_finite", "one_full_epoch_each_completed_trial", "checkpoint_reload_exact", "prediction_order_exact")) or verification["test_predictions"] != 0 or verification["test_used_for_fitting_or_selection"]:
        raise ValueError("Acceptance contract did not pass")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify_acceptance(args.verify)
        print("WP5 acceptance manifest and all artifacts verified.")
    else:
        for path in run(args.config):
            print(path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
