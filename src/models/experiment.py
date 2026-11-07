"""WP6 validation-only tuning and fresh multi-seed final training.

This runner intentionally never constructs ``ModelData(..., "test")``.  WP7 is
the only stage allowed to generate test predictions from the sealed WP6 final
checkpoints.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import statistics
import time
import uuid

import numpy as np
import pandas as pd
import torch

from src.data.harmonize_workbooks import ROOT, load_json, relative_path, sha256_file
from src.evaluation.metrics import point_metrics
from src.features.window_dataset import validate_run
from src.utils.manifest import source_info, validate_manifest, write_json
from .acceptance import MODELS, consumer_digest, environment
from .adapter import ForecastAdapter, ModelData, validate_architecture
from .search import search, validate_search


PURPOSE = "WP6_fair_training_protocol"
PHASES = ("tuning", "final_training")


def validate_config(config):
    required = {"config_version", "purpose", "current_data_pointer", "output_root", "runtime", "training",
                "metrics", "models", "tuning", "final_seeds", "search", "inference_benchmark",
                "adaptations", "protocol_document"}
    if set(config) != required or config["purpose"] != PURPOSE:
        raise ValueError("Missing or unsupported WP6 config fields")
    if set(config["models"]) != set(MODELS):
        raise ValueError("WP6 requires all three architectures")
    if config["runtime"]["precision"] != "float32" or config["runtime"]["device"] not in ("cpu", "cuda"):
        raise ValueError("WP6 requires explicit CPU/CUDA float32 runtime")
    if type(config["runtime"]["threads"]) is not int or config["runtime"]["threads"] < 1 or type(config["runtime"]["deterministic"]) is not bool:
        raise ValueError("Invalid deterministic runtime policy")
    training = config["training"]
    if training.get("max_epochs") != 100 or training.get("patience") != 10 or training.get("min_delta") != 0.0:
        raise ValueError("WP6 requires maximum 100 epochs, patience 10, and min_delta 0.0")
    if training.get("checkpoint_selection") != "validation_rmse" or training.get("optimizer") != "adamw" or training.get("loss") != "mse":
        raise ValueError("WP6 requires validation-RMSE checkpoint selection with AdamW/MSE")
    expected_training = {"batch_size", "max_epochs", "patience", "min_delta", "learning_rate", "weight_decay",
                         "gradient_clip", "optimizer", "loss", "shuffle", "max_seconds", "checkpoint_selection"}
    if set(training) != expected_training or not training["shuffle"]:
        raise ValueError("Invalid WP6 training policy")
    if any(type(training[key]) is not int or training[key] < 1 for key in ("batch_size", "max_epochs", "patience")):
        raise ValueError("Invalid WP6 integer training limits")
    if any(not np.isfinite(training[key]) or training[key] <= 0 for key in ("learning_rate", "gradient_clip", "max_seconds")) or training["weight_decay"] < 0:
        raise ValueError("Invalid WP6 optimization limits")
    if config["metrics"] != {"smape_epsilon": 0.000001, "constant_r2": "null", "mape": "actual_nonzero_only"}:
        raise ValueError("WP6 must retain the shared zero-aware metric policy")
    if config["final_seeds"] != [42, 123, 2026] or type(config["tuning"].get("seed")) is not int:
        raise ValueError("WP6 requires the declared common final seed list and explicit tuning seed")
    if config["tuning"] != {"seed": config["tuning"]["seed"],
                             "strategy": "validation_only_single_seed_then_freeze_before_final_seeds",
                             "fresh_initialization_each_trial": True,
                             "reuse_tuning_checkpoint": False}:
        raise ValueError("Invalid fresh initialization or tuning-freeze policy")
    benchmark = config["inference_benchmark"]
    if benchmark != {"split": "validation", "warmup_runs": 1, "repetitions": 5, "include_adapter_conversion": True}:
        raise ValueError("WP6 must benchmark the shared validation samples with the frozen protocol")
    validate_search(config["search"])
    for name in MODELS:
        validate_architecture(name, {"cardinalities": [1], "encoder_weeks": 12, "horizon_weeks": 1}, config["models"][name])


def _source_hashes(root, config_path, config):
    paths = sorted((root / "src").rglob("*.py")) + [config_path, root / "requirements.txt",
             root / "configs/run-manifest.schema.json", root / config["adaptations"], root / config["protocol_document"]]
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in paths}


def _write_source_snapshot(base, root, sources):
    for name, expected in sources.items():
        source, destination = root / name, base / "source" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as target:
            target.write(source.read_bytes())
        if sha256_file(destination) != expected:
            raise ValueError("Source changed while creating the WP6 snapshot")


def _ensure_sources_unchanged(root, sources):
    if any(sha256_file(root / name) != expected for name, expected in sources.items()):
        raise ValueError("Pinned source/config changed during WP6; cohort is not reproducible")


def _run_base(root, config, model_name, seed, phase):
    if phase not in PHASES:
        raise ValueError("Unknown WP6 phase")
    run_id = (f"{model_name}_{config['config_version']}_seed-{seed}_{phase}_"
              f"{datetime.now(timezone.utc):%Y%m%dt%H%M%S%fz}_{uuid.uuid4().hex[:12]}")
    base = root / config["output_root"] / run_id
    base.mkdir(parents=True, exist_ok=False)
    return run_id, base


def _prediction_rows(run_id, model_name, seed, data, prediction, unit):
    rows = []
    for i, sample in enumerate(prediction["sample_id"]):
        origin = str(data.arrays["origin_time"][i])
        for horizon in range(prediction["prediction"].shape[1]):
            rows.append({"run_id": run_id, "model_name": model_name, "seed": seed, "split": "validation",
                         "sample_id": sample, "series_id": data.arrays["series_id"][i], "origin_time": origin,
                         "target_time": (pd.Timestamp(origin) + pd.Timedelta(weeks=horizon)).strftime("%Y-%m-%d"),
                         "actual": float(data.arrays["y"][i, horizon]),
                         "prediction": float(prediction["prediction"][i, horizon]), "target_unit": unit,
                         "horizon_step": horizon + 1})
    return rows


def _write_predictions(path, rows):
    with path.open("x", encoding="utf-8", newline="") as stream:
        pd.DataFrame(rows).to_csv(stream, index=False)


def _reset_memory(runtime):
    if runtime["device"] == "cuda":
        torch.cuda.reset_peak_memory_stats()


def _peak_memory(runtime):
    if runtime["device"] == "cuda":
        return {"device": "cuda", "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
                "method": "torch.cuda.max_memory_allocated"}
    return {"device": "cpu", "peak_allocated_bytes": None,
            "method": "unavailable_no_validated_platform_specific_collector"}


def _benchmark(model, data, config):
    policy = config["inference_benchmark"]
    for _ in range(policy["warmup_runs"]):
        model.predict(data, batch_size=config["training"]["batch_size"])
    _reset_memory(config["runtime"])
    samples = [model.predict(data, batch_size=config["training"]["batch_size"])["inference_seconds"]
               for _ in range(policy["repetitions"])]
    return {"split": policy["split"], "sample_count": len(data), "warmup_runs": policy["warmup_runs"],
            "repetitions": policy["repetitions"], "include_adapter_conversion": policy["include_adapter_conversion"],
            "seconds": samples, "mean_seconds": statistics.fmean(samples), "median_seconds": statistics.median(samples),
            "min_seconds": min(samples), "max_seconds": max(samples), "peak_memory": _peak_memory(config["runtime"])}


def _write_common_snapshots(base, root, pointer, split_manifest, sources):
    write_json(base / "current-data.snapshot.json", pointer)
    write_json(base / "split_manifest.snapshot.json", split_manifest)
    _write_source_snapshot(base, root, sources)


def _checksums(base):
    return {path.relative_to(base).as_posix(): sha256_file(path) for path in sorted(base.rglob("*")) if path.is_file()}


def _verify_checksums(base, values):
    for name, expected in values.items():
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file() or sha256_file(path) != expected:
            raise ValueError("WP6 artifact checksum mismatch")


def _write_manifest(base, manifest, root):
    validate_manifest(manifest, root=root, require_files=True)
    path = base / "run_manifest.json"
    write_json(path, manifest)
    verify_experiment(path, root=root)
    return path


def _fit_once(name, train, validation, architecture, training, runtime, seed):
    model = ForecastAdapter(name, train.contract, architecture, runtime=runtime, seed=seed)
    _reset_memory(runtime)
    _, fit = model.fit(train, validation, training)
    memory = _peak_memory(runtime)
    prediction = model.predict(validation, batch_size=training["batch_size"])
    metrics = point_metrics(validation.arrays["y"], prediction["prediction"], smape_epsilon=0.000001)
    return model, fit, memory, prediction, metrics


def _prepare_data(root, config):
    pointer_path = relative_path(root, config["current_data_pointer"], description="WP6 pointer")
    pointer = load_json(pointer_path)
    split_path = relative_path(root, pointer["active_split_manifest"], description="WP6 split manifest")
    validate_run(split_path, root=root, verify_inputs=True)
    split_manifest, split_hash = load_json(split_path), sha256_file(split_path)
    data, digests = {}, {}
    for name in MODELS:
        train, validation = (ModelData(split_path, name, split) for split in ("train", "validation"))
        data[name] = (train, validation)
        for split, dataset in (("train", train), ("validation", validation)):
            digest = consumer_digest(dataset, config["training"]["batch_size"])
            if split in digests and digests[split] != digest:
                raise ValueError("WP6 adapters received different shared samples")
            digests[split] = digest
    return pointer, split_path, split_manifest, split_hash, data, digests


def _make_tuning_run(root, config, name, train, validation, pointer, split_manifest, split_path, split_hash, digests, sources, *, resume_from=None):
    seed = config["tuning"]["seed"]
    run_id, base = _run_base(root, config, name, seed, "tuning")
    (base / "trials").mkdir()
    _write_common_snapshots(base, root, pointer, split_manifest, sources)
    write_json(base / "data-contract.json", train.contract)
    records = {}
    if resume_from is not None:
        prior = Path(resume_from).resolve()
        for source_record in sorted((prior / "trials").glob("trial-*/training.json")):
            trial_id = int(source_record.parent.name.removeprefix("trial-"))
            destination = base / "trials" / source_record.parent.name
            destination.mkdir(exist_ok=False)
            for item in source_record.parent.iterdir():
                shutil.copyfile(item, destination / item.name)
            records[trial_id] = load_json(destination / "training.json")

    def evaluate(parameters, trial_id, remaining):
        if trial_id in records:
            record = records[trial_id]
            if record["architecture"].get("d_model") != parameters["d_model"] or record["architecture"].get("n_heads") != parameters["n_heads"] or record["training"].get("learning_rate") != parameters["learning_rate"] or record["training"].get("weight_decay") != parameters["weight_decay"]:
                raise ValueError("Cached WP6 tuning trial differs from regenerated deterministic search")
            return {"validation_rmse": record["metrics"]["rmse"], "validation_mae": record["metrics"]["mae"],
                    "parameter_count": record["metadata"]["parameter_count"]}
        architecture, training = copy.deepcopy(config["models"][name]), copy.deepcopy(config["training"])
        for key, value in parameters.items():
            (architecture if key in architecture else training)[key] = value
        training["max_seconds"] = min(training["max_seconds"], remaining)
        model, fit, memory, prediction, metrics = _fit_once(name, train, validation, architecture, training, config["runtime"], seed)
        trial_dir = base / "trials" / f"trial-{trial_id:03d}"
        trial_dir.mkdir(exist_ok=False)
        model.save(trial_dir / "checkpoint.pt")
        with (trial_dir / "validation_predictions.npz").open("xb") as stream:
            np.savez_compressed(stream, prediction=prediction["prediction"], sample_id=prediction["sample_id"])
        record = {"architecture": architecture, "training": training, "fit": fit, "memory": memory,
                  "metadata": model.metadata(), "metrics": metrics, "inference_seconds": prediction["inference_seconds"]}
        write_json(trial_dir / "training.json", record)
        records[trial_id] = record
        return {"validation_rmse": metrics["rmse"], "validation_mae": metrics["mae"],
                "parameter_count": record["metadata"]["parameter_count"]}

    outcome = search(config["search"], method="moebs" if name == "moebs_transformer" else "random", seed=seed,
                     evaluate=evaluate, on_record=lambda item: write_json(base / "trials" / f"evaluation-{item['trial_id']:03d}.json", item))
    write_json(base / "search.json", outcome)
    completed = sum(entry["status"] == "completed" for entry in outcome["trials"])
    if outcome["evaluations"] != config["search"]["max_evaluations"] or completed != config["search"]["max_evaluations"]:
        raise RuntimeError("WP6 requires the common count of completed tuning evaluations")
    selected = outcome["selected_trial_id"]
    selected_dir, selected_record = base / "trials" / f"trial-{selected:03d}", records[selected]
    model = ForecastAdapter.load(selected_dir / "checkpoint.pt", expected_contract=train.contract, runtime=config["runtime"])
    prediction = model.predict(validation, batch_size=config["training"]["batch_size"])
    with np.load(selected_dir / "validation_predictions.npz", allow_pickle=False) as saved:
        if not np.array_equal(saved["prediction"], prediction["prediction"]) or not np.array_equal(saved["sample_id"], prediction["sample_id"]):
            raise ValueError("Reload changed selected WP6 tuning prediction")
    model.save(base / "checkpoint.pt")
    metrics = point_metrics(validation.arrays["y"], prediction["prediction"], smape_epsilon=config["metrics"]["smape_epsilon"])
    write_json(base / "metrics.json", {"split": "validation", "purpose": PURPOSE, "phase": "tuning", "global": metrics,
               "per_horizon": {str(h + 1): point_metrics(validation.arrays["y"][:, h], prediction["prediction"][:, h],
                                                           smape_epsilon=config["metrics"]["smape_epsilon"])
                                for h in range(train.contract["horizon_weeks"])}})
    _write_predictions(base / "predictions.csv", _prediction_rows(run_id, name, seed, validation, prediction, train.contract["unit"]))
    effective = copy.deepcopy(config)
    effective.update(model_name=name, phase="tuning", split_manifest_path=split_path.relative_to(root).as_posix(),
                     split_manifest_sha256=split_hash, data_contract=train.contract,
                     selected_architecture=selected_record["architecture"], selected_training=selected_record["training"])
    write_json(base / "config.resolved.json", effective)
    verification = {"forward_finite": True, "checkpoint_reload_exact": True, "prediction_order_exact": True,
                    "test_predictions": 0, "test_used_for_fitting_or_selection": False,
                    "completed_trials": completed, "expected_trials": config["search"]["max_evaluations"],
                    "shared_numpy_digest": digests}
    write_json(base / "verification.json", verification)
    _ensure_sources_unchanged(root, sources)
    revision, dirty = source_info(root)
    base_rel = base.relative_to(root).as_posix()
    manifest = {"schema_version": "1", "run_type": "experiment", "run_id": run_id, "model_name": name,
                "config_version": config["config_version"], "seed": seed,
                "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "data_manifest_id": train.manifest["input_manifest_id"], "split_manifest_id": train.manifest_id,
                "feature_contract_version": train.manifest["feature_contract_version"], "source_revision": revision,
                "source_dirty": dirty, "effective_config": effective, "environment": environment(config["runtime"]),
                "timing": {"training_seconds": selected_record["fit"]["training_seconds"], "inference_seconds": prediction["inference_seconds"]},
                "artifacts": {"effective_config": f"{base_rel}/config.resolved.json", "checkpoint": f"{base_rel}/checkpoint.pt",
                              "predictions": f"{base_rel}/predictions.csv", "metrics": f"{base_rel}/metrics.json", "dummy_output": None},
                "wp6": {"purpose": PURPOSE, "phase": "tuning", "verification": verification,
                        "source_code_sha256": sources, "split_manifest_sha256": split_hash, "checksums": _checksums(base),
                        "privacy": "private_no_raw_business_identifiers_in_logs",
                        "availability_assumption": train.manifest["availability_assumption"],
                        "coverage_assumption": train.manifest["coverage_assumption"], "data_digests": digests,
                        "training": selected_record["fit"], "search": outcome, "selected_trial_id": selected,
                        "search_seconds": outcome["elapsed_seconds"],
                        "total_trial_training_seconds": sum(record["fit"]["training_seconds"] for record in records.values()),
                        "completed_trials": completed, "fresh_initialization": True,
                        "tuning": {"strategy": config["tuning"]["strategy"], "reuse_tuning_checkpoint": False},
                        "memory": selected_record["memory"]}}
    return _write_manifest(base, manifest, root)


def resume_tuning(config_path, prior_base, *, root=ROOT):
    """Finish a bounded tuning run from completed private trial artifacts."""
    root, config_path, prior_base = Path(root).resolve(), Path(config_path).resolve(), Path(prior_base).resolve()
    config = load_json(config_path)
    validate_config(config)
    name = next((candidate for candidate in MODELS if prior_base.name.startswith(candidate + "_")), None)
    if name is None:
        raise ValueError("Resume directory does not identify a supported model")
    sources = _source_hashes(root, config_path, config)
    pointer, split_path, split_manifest, split_hash, data, digests = _prepare_data(root, config)
    train, validation = data[name]
    path = _make_tuning_run(root, config, name, train, validation, pointer, split_manifest, split_path, split_hash, digests, sources, resume_from=prior_base)
    print(f"WP6 {name}: resumed tuning completed.", flush=True)
    return path


def _make_final_run(root, config, name, seed, tuning_manifest_path, train, validation, pointer, split_manifest, split_path, split_hash, digests, sources):
    tuning_manifest = load_json(tuning_manifest_path)
    selected_architecture = copy.deepcopy(tuning_manifest["effective_config"]["selected_architecture"])
    selected_trial_training = tuning_manifest["effective_config"]["selected_training"]
    selected_training = copy.deepcopy(config["training"])
    for key in ("learning_rate", "weight_decay"):
        selected_training[key] = selected_trial_training[key]
    if tuning_manifest["wp6"]["phase"] != "tuning" or selected_trial_training["max_epochs"] != 100:
        raise ValueError("Final WP6 training requires a sealed 100-epoch tuning selection")
    run_id, base = _run_base(root, config, name, seed, "final_training")
    _write_common_snapshots(base, root, pointer, split_manifest, sources)
    write_json(base / "data-contract.json", train.contract)
    model, fit, memory, prediction, metrics = _fit_once(name, train, validation, selected_architecture, selected_training, config["runtime"], seed)
    model.save(base / "checkpoint.pt")
    restored = ForecastAdapter.load(base / "checkpoint.pt", expected_contract=train.contract, runtime=config["runtime"])
    reload_prediction = restored.predict(validation, batch_size=selected_training["batch_size"])
    if not np.array_equal(prediction["prediction"], reload_prediction["prediction"]) or not np.array_equal(prediction["sample_id"], reload_prediction["sample_id"]):
        raise ValueError("Reload changed final WP6 validation prediction")
    benchmark = _benchmark(restored, validation, config)
    write_json(base / "metrics.json", {"split": "validation", "purpose": PURPOSE, "phase": "final_training", "global": metrics,
               "per_horizon": {str(h + 1): point_metrics(validation.arrays["y"][:, h], prediction["prediction"][:, h],
                                                           smape_epsilon=config["metrics"]["smape_epsilon"])
                                for h in range(train.contract["horizon_weeks"])}})
    _write_predictions(base / "predictions.csv", _prediction_rows(run_id, name, seed, validation, prediction, train.contract["unit"]))
    effective = copy.deepcopy(config)
    effective.update(model_name=name, phase="final_training", split_manifest_path=split_path.relative_to(root).as_posix(),
                     split_manifest_sha256=split_hash, data_contract=train.contract, selected_architecture=selected_architecture,
                     selected_training=selected_training, tuning_manifest_path=tuning_manifest_path.relative_to(root).as_posix(),
                     tuning_manifest_sha256=sha256_file(tuning_manifest_path))
    write_json(base / "config.resolved.json", effective)
    verification = {"forward_finite": True, "checkpoint_reload_exact": True, "prediction_order_exact": True,
                    "test_predictions": 0, "test_used_for_fitting_or_selection": False, "shared_numpy_digest": digests,
                    "fresh_initialization": True, "epochs_completed": fit["epochs_completed"]}
    write_json(base / "verification.json", verification)
    _ensure_sources_unchanged(root, sources)
    revision, dirty = source_info(root)
    base_rel = base.relative_to(root).as_posix()
    manifest = {"schema_version": "1", "run_type": "experiment", "run_id": run_id, "model_name": name,
                "config_version": config["config_version"], "seed": seed,
                "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "data_manifest_id": train.manifest["input_manifest_id"], "split_manifest_id": train.manifest_id,
                "feature_contract_version": train.manifest["feature_contract_version"], "source_revision": revision,
                "source_dirty": dirty, "effective_config": effective, "environment": environment(config["runtime"]),
                "timing": {"training_seconds": fit["training_seconds"], "inference_seconds": benchmark["median_seconds"]},
                "artifacts": {"effective_config": f"{base_rel}/config.resolved.json", "checkpoint": f"{base_rel}/checkpoint.pt",
                              "predictions": f"{base_rel}/predictions.csv", "metrics": f"{base_rel}/metrics.json", "dummy_output": None},
                "wp6": {"purpose": PURPOSE, "phase": "final_training", "verification": verification,
                        "source_code_sha256": sources, "split_manifest_sha256": split_hash, "checksums": _checksums(base),
                        "privacy": "private_no_raw_business_identifiers_in_logs",
                        "availability_assumption": train.manifest["availability_assumption"],
                        "coverage_assumption": train.manifest["coverage_assumption"], "data_digests": digests,
                        "training": fit, "fresh_initialization": True,
                        "tuning": {"manifest_path": tuning_manifest_path.relative_to(root).as_posix(),
                                   "manifest_sha256": sha256_file(tuning_manifest_path),
                                   "selected_trial_id": tuning_manifest["wp6"]["selected_trial_id"],
                                   "checkpoint_reused": False}, "inference_benchmark": benchmark, "memory": memory}}
    return _write_manifest(base, manifest, root)


def verify_experiment(manifest_path, *, root=ROOT):
    manifest_path, root = Path(manifest_path).resolve(), Path(root).resolve()
    manifest = load_json(manifest_path)
    validate_manifest(manifest, root=root, require_files=True)
    if manifest["run_type"] != "experiment" or manifest["wp6"]["purpose"] != PURPOSE:
        raise ValueError("Not a WP6 experiment manifest")
    base, wp6, verification = manifest_path.parent, manifest["wp6"], manifest["wp6"]["verification"]
    _verify_checksums(base, wp6["checksums"])
    if verification.get("test_predictions") != 0 or verification.get("test_used_for_fitting_or_selection"):
        raise ValueError("WP6 must not predict or select with test data")
    if not all(verification.get(key) for key in ("forward_finite", "checkpoint_reload_exact", "prediction_order_exact")):
        raise ValueError("WP6 model verification failed")
    split_path = relative_path(root, manifest["effective_config"]["split_manifest_path"], description="WP6 split manifest")
    if sha256_file(split_path) != wp6["split_manifest_sha256"]:
        raise ValueError("WP6 split manifest changed")
    if wp6["phase"] == "tuning":
        if wp6["completed_trials"] != manifest["effective_config"]["search"]["max_evaluations"]:
            raise ValueError("WP6 tuning did not complete the common evaluation budget")
    else:
        tuning = wp6["tuning"]
        path = relative_path(root, tuning["manifest_path"], description="WP6 tuning manifest")
        if sha256_file(path) != tuning["manifest_sha256"] or load_json(path)["wp6"]["phase"] != "tuning":
            raise ValueError("WP6 final run has an invalid tuning lineage")
        if not wp6["fresh_initialization"] or tuning["checkpoint_reused"]:
            raise ValueError("WP6 final run did not start from fresh initialization")
    return manifest


def verify_cohort(cohort_path, *, root=ROOT):
    cohort_path, root = Path(cohort_path).resolve(), Path(root).resolve()
    cohort = load_json(cohort_path)
    if cohort.get("stage") == "WP6_certified_cohort":
        from .cohort import verify
        return verify(cohort_path, root=root)
    if cohort.get("stage") != "WP6_fair_training_cohort" or cohort.get("purpose") != PURPOSE:
        raise ValueError("Not a WP6 cohort")
    if cohort.get("final_seeds") != [42, 123, 2026] or cohort.get("test_predictions") != 0:
        raise ValueError("WP6 cohort seed or test policy differs from the frozen protocol")
    entries = cohort.get("tuning_runs", []) + cohort.get("final_runs", [])
    if len(cohort.get("tuning_runs", [])) != len(MODELS) or len(cohort.get("final_runs", [])) != len(MODELS) * 3:
        raise ValueError("WP6 cohort has an incomplete model/seed grid")
    manifests = []
    for entry in entries:
        path = relative_path(root, entry["manifest_path"], description="WP6 cohort member")
        if sha256_file(path) != entry["sha256"]:
            raise ValueError("WP6 cohort member manifest checksum changed")
        manifests.append(verify_experiment(path, root=root))
    tuning = [m for m in manifests if m["wp6"]["phase"] == "tuning"]
    finals = [m for m in manifests if m["wp6"]["phase"] == "final_training"]
    if {m["model_name"] for m in tuning} != set(MODELS) or {(m["model_name"], m["seed"]) for m in finals} != {(name, seed) for name in MODELS for seed in cohort["final_seeds"]}:
        raise ValueError("WP6 cohort model/seed pairs are not comparable")
    if len({m["split_manifest_id"] for m in manifests}) != 1 or len({str(m["wp6"]["data_digests"]) for m in manifests}) != 1:
        raise ValueError("WP6 cohort data binding differs across models or seeds")
    return cohort


def train_one(config_path, name, seed, tuning_manifest_path, *, root=ROOT):
    """Train exactly one final model/seed from an explicitly pinned tuning run."""
    root, config_path = Path(root).resolve(), Path(config_path).resolve()
    config = load_json(config_path)
    validate_config(config)
    if name not in MODELS or seed not in config["final_seeds"]:
        raise ValueError("Select one configured model and final seed")
    output_root = relative_path(root, config["output_root"], description="WP6 output")
    if output_root != root / "artifacts/runs":
        raise ValueError("WP6 outputs must remain private under artifacts/runs")
    tuning_path = Path(tuning_manifest_path)
    tuning_path = (tuning_path if tuning_path.is_absolute() else root / tuning_path).resolve()
    if not tuning_path.is_relative_to(output_root):
        raise ValueError("Tuning manifest must remain in private artifacts/runs")
    tuning = verify_experiment(tuning_path, root=root)
    if tuning["model_name"] != name or tuning["wp6"]["phase"] != "tuning":
        raise ValueError("Select the tuning manifest for this model")
    if any(tuning["effective_config"].get(key) != value for key, value in config.items()):
        raise ValueError("Training config differs from the frozen tuning protocol")
    sources = _source_hashes(root, config_path, config)
    pointer, split_path, split_manifest, split_hash, data, digests = _prepare_data(root, config)
    train, validation = data[name]
    if (tuning["wp6"]["split_manifest_sha256"] != split_hash
            or tuning["effective_config"]["data_contract"] != train.contract
            or tuning["wp6"]["data_digests"] != digests):
        raise ValueError("Final training must use the same immutable data as tuning")
    print(f"WP6 starting one final run: {name}, seed {seed}.", flush=True)
    path = _make_final_run(root, config, name, seed, tuning_path, train, validation,
                           pointer, split_manifest, split_path, split_hash, digests, sources)
    print(f"WP6 final run verified: {path.relative_to(root).as_posix()}", flush=True)
    return path


def run(config_path, *, root=ROOT):
    root, config_path = Path(root).resolve(), Path(config_path).resolve()
    config = load_json(config_path)
    validate_config(config)
    output_root = relative_path(root, config["output_root"], description="WP6 output")
    if output_root != root / "artifacts/runs":
        raise ValueError("WP6 outputs must remain private under artifacts/runs")
    sources = _source_hashes(root, config_path, config)
    pointer, split_path, split_manifest, split_hash, data, digests = _prepare_data(root, config)
    tuning_paths = []
    for name in MODELS:
        train, validation = data[name]
        tuning_paths.append(_make_tuning_run(root, config, name, train, validation, pointer, split_manifest, split_path, split_hash, digests, sources))
        print(f"WP6 {name}: validation-only tuning completed with {config['search']['max_evaluations']} matched trials.", flush=True)
    final_paths = []
    tuning_by_name = {load_json(path)["model_name"]: path for path in tuning_paths}
    for name in MODELS:
        train, validation = data[name]
        for seed in config["final_seeds"]:
            final_paths.append(_make_final_run(root, config, name, seed, tuning_by_name[name], train, validation, pointer, split_manifest, split_path, split_hash, digests, sources))
            print(f"WP6 {name}: fresh final seed {seed} completed.", flush=True)
    _ensure_sources_unchanged(root, sources)
    cohort = {"stage": "WP6_fair_training_cohort", "purpose": PURPOSE, "config_version": config["config_version"],
              "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "final_seeds": config["final_seeds"],
              "tuning_seed": config["tuning"]["seed"], "split_manifest_id": split_manifest["manifest_id"],
              "split_manifest_sha256": split_hash, "shared_digests": digests, "test_predictions": 0,
              "tuning_runs": [{"model_name": load_json(path)["model_name"], "manifest_path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)} for path in tuning_paths],
              "final_runs": [{"model_name": load_json(path)["model_name"], "seed": load_json(path)["seed"], "manifest_path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)} for path in final_paths]}
    cohort_path = output_root / f"wp6-cohort_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}.json"
    write_json(cohort_path, cohort)
    verify_cohort(cohort_path, root=root)
    print(f"WP6 matched fair-training cohort: {cohort_path.relative_to(root).as_posix()}", flush=True)
    return cohort_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", type=Path)
    group.add_argument("--verify", type=Path)
    group.add_argument("--verify-cohort", type=Path)
    group.add_argument("--resume-tuning", type=Path)
    group.add_argument("--train-one", choices=MODELS,
                       help="Train exactly one final model; requires --seed and --tuning-manifest")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--tuning-manifest", type=Path)
    parser.add_argument("--training-config", type=Path, default=ROOT / "configs/models.wp6.v1.json")
    args = parser.parse_args()
    if args.train_one:
        if args.seed is None or args.tuning_manifest is None:
            parser.error("--train-one requires --seed and --tuning-manifest")
        train_one(args.training_config, args.train_one, args.seed, args.tuning_manifest)
    elif args.seed is not None or args.tuning_manifest is not None:
        parser.error("--seed and --tuning-manifest are only valid with --train-one")
    elif args.config:
        run(args.config)
    elif args.resume_tuning:
        resume_tuning(ROOT / "configs/models.wp6.v1.json", args.resume_tuning)
    elif args.verify:
        verify_experiment(args.verify)
        print("WP6 experiment manifest and artifacts verified.")
    else:
        verify_cohort(args.verify_cohort)
        print("WP6 fair-training cohort verified.")


if __name__ == "__main__":
    main()
