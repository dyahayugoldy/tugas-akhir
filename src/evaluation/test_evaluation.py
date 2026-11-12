"""Evaluate every certified final WP6 checkpoint on the sealed WP4 test set."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import statistics
import time
import uuid

import numpy as np
import pandas as pd

from src.data.harmonize_workbooks import ROOT, load_json, relative_path, sha256_file
from src.evaluation.metrics import point_metrics
from src.features.window_dataset import validate_run
from src.models.acceptance import MODELS, consumer_digest, environment
from src.models.adapter import ForecastAdapter, ModelData
from src.models.experiment import verify_cohort, verify_experiment
from src.utils.manifest import source_info, validate_manifest, write_json


PURPOSE = "WP7_frozen_test_evaluation"
METRICS = ("mae", "rmse", "mape", "smape", "r2")
RESOLVED_FIELDS = ("model_name", "seed", "split_manifest_path", "data_contract",
                   "upstream_manifest_path", "upstream_manifest_sha256")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_path(root, name):
    path = relative_path(root, name, description="WP7 private artifact")
    require(path.is_relative_to(root / "artifacts/runs"), "WP7 outputs/cohort must remain private")
    return path


def validate_config(config):
    fields = {"config_version", "purpose", "cohort_path", "cohort_sha256", "output_root", "models", "seeds",
              "runtime", "batch_size", "metrics", "prediction_policy", "inference_benchmark",
              "error_analysis", "protocol_document"}
    require(set(config) == fields and config["purpose"] == PURPOSE, "Invalid WP7 config fields/purpose")
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]*", config["config_version"]), "Invalid config version")
    require(re.fullmatch(r"[a-f0-9]{64}", config["cohort_sha256"]), "Pin the certified cohort checksum")
    require(config["output_root"] == "artifacts/runs", "WP7 outputs must remain private")
    require(config["models"] == list(MODELS) and config["seeds"] == [42, 123, 2026], "Evaluate the complete model/seed grid")
    require(config["runtime"] == {"device": "cpu", "precision": "float32", "threads": 2, "deterministic": True},
            "Retain the frozen WP6 runtime")
    require(type(config["batch_size"]) is int and config["batch_size"] > 0, "Invalid batch size")
    require(config["metrics"] == {"smape_epsilon": 1e-6, "constant_r2": "null", "mape": "actual_nonzero_only"},
            "Retain the frozen zero-aware metric policy")
    require(config["prediction_policy"] == "original_pcs_unclipped_no_rounding", "Do not adjust test predictions")
    require(config["inference_benchmark"] == {"warmup_runs": 1, "repetitions": 5, "include_adapter_conversion": True},
            "Invalid inference measurement policy")
    analysis = config["error_analysis"]
    require(set(analysis) == {"minimum_group_samples", "minimum_group_series", "train_sparse_zero_fraction",
                             "train_target_outlier_quantile", "residual_quantiles", "worst_error_rows"},
            "Invalid error analysis fields")
    for key in ("minimum_group_samples", "minimum_group_series", "worst_error_rows"):
        require(type(analysis[key]) is int and analysis[key] > 0, "Invalid diagnostic count threshold")
    require(0 < analysis["train_sparse_zero_fraction"] <= 1 and 0 < analysis["train_target_outlier_quantile"] < 1,
            "Invalid train diagnostic threshold")
    q = analysis["residual_quantiles"]
    require(isinstance(q, list) and bool(q) and all(0 <= v <= 1 for v in q) and q == sorted(set(q)),
            "Invalid residual quantiles")


def validate_grid(entries, config):
    pairs = [(entry["model_name"], entry["seed"]) for entry in entries]
    expected = {(name, seed) for name in config["models"] for seed in config["seeds"]}
    require(len(pairs) == len(expected) and set(pairs) == expected, "Incomplete or duplicate model/seed grid")


def prepare_inputs(config, root):
    cohort_path = private_path(root, config["cohort_path"])
    require(sha256_file(cohort_path) == config["cohort_sha256"], "Certified cohort checksum mismatch")
    receipt = verify_cohort(cohort_path, root=root)
    require(receipt["stage"] == "WP6_certified_cohort" and receipt["test_predictions"] == 0,
            "WP7 requires the certified WP6 cohort")
    validate_grid(receipt["final_runs"], config)
    members, bindings = [], set()
    for entry in receipt["final_runs"]:
        path = private_path(root, entry["manifest_path"])
        require(sha256_file(path) == entry["sha256"], "Final WP6 member checksum mismatch")
        manifest = verify_experiment(path, root=root)
        require(manifest["wp6"]["phase"] == "final_training" and manifest["model_name"] == entry["model_name"]
                and manifest["seed"] == entry["seed"], "Only matching final WP6 checkpoints may be evaluated")
        effective = manifest["effective_config"]
        require(effective["runtime"] == config["runtime"] and effective["metrics"] == config["metrics"]
                and effective["training"]["batch_size"] == config["batch_size"], "WP7 changed the frozen evaluation contract")
        for name, expected in manifest["wp6"]["source_code_sha256"].items():
            if name.startswith("src/") and name != "src/models/experiment.py":
                require(sha256_file(root / name) == expected, "Model/data/scoring implementation changed since WP6")
        bindings.add((effective["split_manifest_path"], manifest["wp6"]["split_manifest_sha256"]))
        members.append((path, manifest))
    require(len(bindings) == 1, "Final checkpoints use different data/splits")
    split_name, digest = bindings.pop()
    split_path = relative_path(root, split_name, description="WP7 sealed split")
    require(sha256_file(split_path) == digest, "WP6-bound split changed")
    validate_run(split_path, root=root, verify_inputs=True)
    return receipt, members, split_path


def build_context(split_path, root, config):
    """Diagnostic categories and thresholds only; never passed into a model."""
    manifest = load_json(split_path)
    parent_path = relative_path(root, manifest["input_manifest"], description="WP3 diagnostic panel")
    parent = load_json(parent_path)
    for name in ("panel/panel_weekly.csv.gz", "panel/series_registry.csv.gz"):
        require(sha256_file(parent_path.parent / name) == parent["outputs"][name], "Diagnostic panel checksum mismatch")
    panel = pd.read_csv(parent_path.parent / "panel/panel_weekly.csv.gz", dtype=str, keep_default_na=False)
    registry = pd.read_csv(parent_path.parent / "panel/series_registry.csv.gz", dtype=str, keep_default_na=False)
    prep = load_json(split_path.parent / "preprocessing.json")
    fields = load_json(split_path.parent / "tensor_schema.json")["static_categorical"]
    require(not registry.series_id.duplicated().any(), "Duplicate diagnostic series")
    bounds = manifest["split"]["train"]
    cutoff = (pd.Timestamp(bounds["end"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    train = panel.loc[panel.week_start.ge(bounds["start"]) & panel.week_end.le(bounds["end"])
                      & panel.available_at.le(cutoff)].copy()
    train["value"] = pd.to_numeric(train[prep["target_field"]], errors="raise")
    require(len(train) > 0 and np.isfinite(train.value).all(), "Invalid train diagnostic values")
    fractions = train.assign(zero=train.value.eq(0)).groupby("series_id").zero.mean()
    threshold = config["error_analysis"]["train_sparse_zero_fraction"]
    categories = registry[["series_id", *fields]].copy()
    categories["train_zero_fraction"] = categories.series_id.map(fractions)
    categories["sparsity_group"] = np.where(categories.train_zero_fraction.isna(), "no_train_history",
                                           np.where(categories.train_zero_fraction.ge(threshold), "sparse_train", "other_train"))
    return categories, {"threshold_source": "unique_train_panel_rows_available_at_train_cutoff",
                        "train_rows": len(train), "train_cutoff": cutoff,
                        "sparse_zero_fraction": threshold,
                        "target_tail_quantile": config["error_analysis"]["train_target_outlier_quantile"],
                        "target_tail_threshold": float(train.value.quantile(config["error_analysis"]["train_target_outlier_quantile"]))}


def prediction_frame(run_id, data, result, seed):
    require(np.array_equal(result["sample_id"], data.arrays["sample_id"]), "Prediction order changed")
    horizon = data.contract["horizon_weeks"]
    require(result["prediction"].shape == data.arrays["y"].shape == (len(data), horizon), "Forecast shape mismatch")
    rows = []
    for i, sample in enumerate(result["sample_id"]):
        origin = str(data.arrays["origin_time"][i])
        for h in range(horizon):
            actual, predicted = float(data.arrays["y"][i, h]), float(result["prediction"][i, h])
            rows.append({"run_id": run_id, "model_name": data.model_name, "seed": seed, "split": "test",
                         "sample_id": sample, "series_id": data.arrays["series_id"][i], "origin_time": origin,
                         "target_time": (pd.Timestamp(origin) + pd.Timedelta(weeks=h)).strftime("%Y-%m-%d"),
                         "horizon_step": h + 1, "target_unit": data.contract["unit"], "actual": actual,
                         "prediction": predicted, "residual": predicted - actual, "absolute_error": abs(predicted - actual)})
    frame = pd.DataFrame(rows)
    require(not frame.duplicated(["sample_id", "horizon_step"]).any(), "Duplicate forecast rows")
    bounds = data.manifest["split"]["test"]
    require(frame.target_time.ge(bounds["start"]).all()
            and (pd.to_datetime(frame.target_time) + pd.Timedelta(days=6)).le(pd.Timestamp(bounds["end"])).all(),
            "Forecast target crosses the sealed test split")
    return frame


def metrics_for(frame, epsilon):
    return point_metrics(frame.actual.to_numpy(), frame.prediction.to_numpy(), smape_epsilon=epsilon)


def analyze(frame, categories, thresholds, config):
    policy, epsilon = config["error_analysis"], config["metrics"]["smape_epsilon"]
    data = frame.merge(categories, on="series_id", how="left", validate="many_to_one", indicator=True)
    require(data._merge.eq("both").all(), "Missing diagnostic series metadata")
    data["target_month"] = data.target_time.str[:7]
    data["demand_group"] = np.where(data.actual.eq(0), "actual_zero", "actual_nonzero")
    data["target_tail"] = np.where(data.actual.gt(thresholds["target_tail_threshold"]), "above_train_tail", "within_train_tail")
    groups = {}
    dimensions = [c for c in categories if c not in ("series_id", "train_zero_fraction")]
    for field in ["horizon_step", *dimensions, "target_month", "demand_group", "target_tail", "series_id"]:
        records = []
        for key, group in data.groupby(field, sort=True, dropna=False):
            enough = len(group) >= policy["minimum_group_samples"] and group.series_id.nunique() >= (
                1 if field == "series_id" else policy["minimum_group_series"])
            records.append({"group": str(key), "count": len(group), "series_count": int(group.series_id.nunique()),
                            "suppressed_small_group": not enough, "metrics": metrics_for(group, epsilon) if enough else None})
        groups[field] = records
    residuals = data.residual.to_numpy(dtype=np.float64)
    ordered = data.sort_values(["series_id", "horizon_step", "target_time"])
    by = ordered.groupby(["series_id", "horizon_step"])
    previous = by.residual.shift(1)
    adjacent = (pd.to_datetime(ordered.target_time) - pd.to_datetime(by.target_time.shift(1))).dt.days.eq(7)
    x, y = previous.loc[adjacent].to_numpy(), ordered.loc[adjacent, "residual"].to_numpy()
    correlation = float(np.corrcoef(x, y)[0, 1]) if len(x) > 1 and x.std() > 0 and y.std() > 0 else None
    return {"residual_definition": "prediction_minus_actual", "thresholds": thresholds,
            "residual": {"mean": float(residuals.mean()), "std_ddof1": float(residuals.std(ddof=1)) if len(residuals) > 1 else None,
                         "quantiles": {str(q): float(np.quantile(residuals, q)) for q in policy["residual_quantiles"]},
                         "maximum_absolute_error": float(np.abs(residuals).max()), "lag1_adjacent_pairs": len(x),
                         "pooled_lag1_correlation": correlation},
            "negative_predictions": int(data.prediction.lt(0).sum()), "actual_zero_count": int(data.actual.eq(0).sum()),
            "groups": groups}


def summarize(manifests, root):
    rows = []
    for manifest in manifests:
        metrics = load_json(root / manifest["artifacts"]["metrics"])["global"]
        w = manifest["wp7"]
        rows.append({"run_id": manifest["run_id"], "model_name": manifest["model_name"], "seed": manifest["seed"],
                     **{k: metrics[k] for k in (*METRICS, "count", "mape_count", "mape_coverage", "negative_prediction_count")},
                     "wp6_training_seconds": w["upstream_training_seconds"], "parameter_count": w["parameter_count"],
                     "test_inference_median_seconds": manifest["timing"]["inference_seconds"]})
    frame = pd.DataFrame(rows).sort_values(["model_name", "seed"]).reset_index(drop=True)
    for key in METRICS:
        frame[key] = pd.to_numeric(frame[key], errors="raise")
    summary = []
    for name, group in frame.groupby("model_name", sort=True):
        item = {"model_name": name, "seeds": len(group), "count_per_seed": int(group["count"].iloc[0])}
        require(group["count"].nunique() == 1, "Seed evaluation sample counts differ")
        for key in (*METRICS, "wp6_training_seconds", "test_inference_median_seconds", "negative_prediction_count"):
            values = group[key].dropna()
            item[key + "_defined_seeds"] = len(values)
            item[key + "_mean"] = float(values.mean()) if len(values) else None
            item[key + "_std_ddof1"] = float(values.std(ddof=1)) if len(values) > 1 else None
        summary.append(item)
    summary_frame = pd.DataFrame(summary)
    for key in summary_frame.columns.drop("model_name"):
        summary_frame[key] = pd.to_numeric(summary_frame[key], errors="raise")
    return frame, summary_frame


def write_csv(path, frame):
    with path.open("x", encoding="utf-8", newline="") as stream:
        frame.to_csv(stream, index=False)


def checksums(base):
    return {p.relative_to(base).as_posix(): sha256_file(p) for p in sorted(base.rglob("*"))
            if p.is_file() and p != base / "run_manifest.json"}


def check_files(base, expected):
    require(checksums(base) == expected, "WP7 artifact checksum/file set mismatch")


def benchmark(model, data, config):
    policy, batch_size = config["inference_benchmark"], config["batch_size"]
    for _ in range(policy["warmup_runs"]):
        model.predict(data, batch_size=batch_size)
    results = [model.predict(data, batch_size=batch_size) for _ in range(policy["repetitions"])]
    for result in results[1:]:
        np.testing.assert_array_equal(results[0]["sample_id"], result["sample_id"])
        np.testing.assert_array_equal(results[0]["prediction"], result["prediction"])
    seconds = [result["inference_seconds"] for result in results]
    return results[0], {**policy, "split": "test", "sample_count": len(data), "seconds": seconds,
                        "median_seconds": statistics.median(seconds), "mean_seconds": statistics.fmean(seconds),
                        "timing_scope": "adapter_batch_conversion_forward_collection_excludes_inverse_scaling_io_metrics"}


def evaluate_one(config, upstream_path, upstream, split_path, data, categories, thresholds, sources, root):
    name, seed = upstream["model_name"], upstream["seed"]
    run_id = f"{name}_{config['config_version']}_seed-{seed}_{datetime.now(timezone.utc):%Y%m%dt%H%M%S%fz}_{uuid.uuid4().hex[:12]}"
    base = private_path(root, config["output_root"] + "/" + run_id)
    base.mkdir(parents=True, exist_ok=False)
    data.model_name = name
    model = ForecastAdapter.load(upstream_path.parent / "checkpoint.pt", expected_contract=data.contract, runtime=config["runtime"])
    require(model.name == name and model.seed == seed and model.fit_record == upstream["wp6"]["training"],
            "Checkpoint metadata differs from certified final run")
    result, timing = benchmark(model, data, config)
    frame = prediction_frame(run_id, data, result, seed)
    epsilon = config["metrics"]["smape_epsilon"]
    metrics = {"purpose": PURPOSE, "split": "test", "global": metrics_for(frame, epsilon),
               "per_horizon": {str(h): metrics_for(g, epsilon) for h, g in frame.groupby("horizon_step")}}
    for filename in ("checkpoint.pt", "checkpoint.pt.sha256"):
        shutil.copyfile(upstream_path.parent / filename, base / filename)
    write_json(base / "upstream-manifest.snapshot.json", upstream)
    write_json(base / "split-manifest.snapshot.json", data.manifest)
    write_json(base / "data-contract.json", data.contract)
    for filename, digest in sources.items():
        target = base / "source" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / filename, target)
        require(sha256_file(target) == digest, "Source changed during WP7 snapshot")
    effective = {**config, "model_name": name, "seed": seed,
                 "split_manifest_path": split_path.relative_to(root).as_posix(), "data_contract": data.contract,
                 "upstream_manifest_path": upstream_path.relative_to(root).as_posix(),
                 "upstream_manifest_sha256": sha256_file(upstream_path)}
    write_json(base / "config.resolved.json", effective)
    write_csv(base / "predictions.csv", frame)
    write_json(base / "metrics.json", metrics)
    write_json(base / "error-analysis.json", analyze(frame, categories, thresholds, config))
    write_csv(base / "worst-errors.csv", frame.sort_values("absolute_error", ascending=False, kind="stable").head(config["error_analysis"]["worst_error_rows"]))
    revision, dirty = source_info(root)
    rel = base.relative_to(root).as_posix()
    manifest = {"schema_version": "1", "run_type": "experiment", "run_id": run_id, "model_name": name,
                "config_version": config["config_version"], "seed": seed,
                "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "data_manifest_id": data.manifest["input_manifest_id"], "split_manifest_id": data.manifest_id,
                "feature_contract_version": data.manifest["feature_contract_version"], "source_revision": revision,
                "source_dirty": dirty, "effective_config": effective, "environment": environment(config["runtime"]),
                "timing": {"training_seconds": 0, "inference_seconds": timing["median_seconds"]},
                "artifacts": {"effective_config": rel + "/config.resolved.json", "checkpoint": rel + "/checkpoint.pt",
                              "predictions": rel + "/predictions.csv", "metrics": rel + "/metrics.json", "dummy_output": None},
                "wp7": {"purpose": PURPOSE, "cohort_sha256": config["cohort_sha256"],
                        "split_manifest_sha256": sha256_file(split_path), "test_digest": consumer_digest(data, config["batch_size"]),
                        "checkpoint_sha256": sha256_file(base / "checkpoint.pt"),
                        "upstream_training_seconds": upstream["timing"]["training_seconds"],
                        "parameter_count": model.metadata()["parameter_count"], "inference_benchmark": timing,
                        "test_used_for_fitting_or_selection": False, "prediction_policy": config["prediction_policy"],
                        "coverage_assumption": data.manifest["coverage_assumption"],
                        "availability_assumption": data.manifest["availability_assumption"],
                        "source_code_sha256": sources, "checksums": checksums(base),
                        "privacy": "private_no_raw_business_identifiers_in_logs"}}
    validate_manifest(manifest, root=root, require_files=True)
    path = base / "run_manifest.json"
    write_json(path, manifest)
    verify_run(path, root=root, context=(categories, thresholds))
    print(f"WP7 {name}, seed {seed}: {len(frame)} ordered test forecasts and artifacts verified.", flush=True)
    return path


def verify_run(path, *, root=ROOT, context=None):
    root, path = Path(root).resolve(), Path(path).resolve()
    private_path(root, path.relative_to(root).as_posix())
    manifest = load_json(path)
    validate_manifest(manifest, root=root, require_files=True)
    require("wp7" in manifest and "wp6" not in manifest, "Expected a WP7 evaluation manifest")
    w, config, base = manifest["wp7"], manifest["effective_config"], path.parent
    plain = {key: value for key, value in config.items() if key not in RESOLVED_FIELDS}
    validate_config(plain)
    check_files(base, w["checksums"])
    require(manifest["run_id"] == base.name and w["purpose"] == PURPOSE and not w["test_used_for_fitting_or_selection"]
            and manifest["timing"]["training_seconds"] == 0, "Invalid WP7 identity or fitting policy")
    cohort_path = private_path(root, config["cohort_path"])
    require(sha256_file(cohort_path) == config["cohort_sha256"] == w["cohort_sha256"], "Certified cohort changed")
    upstream_path = private_path(root, config["upstream_manifest_path"])
    require(sha256_file(upstream_path) == config["upstream_manifest_sha256"], "Upstream member changed")
    member = {"model_name": manifest["model_name"], "seed": manifest["seed"],
              "manifest_path": config["upstream_manifest_path"], "sha256": config["upstream_manifest_sha256"]}
    require(member in load_json(cohort_path)["final_runs"], "WP7 member is outside certified final grid")
    upstream = verify_experiment(upstream_path, root=root)
    require(upstream == load_json(base / "upstream-manifest.snapshot.json") and upstream["wp6"]["phase"] == "final_training",
            "Wrong upstream snapshot/phase")
    require(config["model_name"] == manifest["model_name"] == upstream["model_name"]
            and config["seed"] == manifest["seed"] == upstream["seed"]
            and config["runtime"] == upstream["effective_config"]["runtime"]
            and config["metrics"] == upstream["effective_config"]["metrics"]
            and config["batch_size"] == upstream["effective_config"]["training"]["batch_size"],
            "Run identity/runtime/metric policy differs from upstream")
    require(sha256_file(base / "checkpoint.pt") == w["checkpoint_sha256"] == sha256_file(upstream_path.parent / "checkpoint.pt"),
            "WP7 checkpoint is not byte-identical to certified WP6 checkpoint")
    for name, digest in w["source_code_sha256"].items():
        require(sha256_file(base / "source" / name) == digest, "Source snapshot changed")
        if name.startswith("src/"):
            require(sha256_file(root / name) == digest, "Use the pinned WP7 implementation to verify")
    split_path = relative_path(root, config["split_manifest_path"], description="WP7 split")
    require(sha256_file(split_path) == w["split_manifest_sha256"] == upstream["wp6"]["split_manifest_sha256"], "Bound split changed")
    data = ModelData(split_path, manifest["model_name"], "test")
    data.model_name = manifest["model_name"]
    require(data.contract == config["data_contract"] == load_json(base / "data-contract.json"), "Data contract changed")
    require(data.manifest == load_json(base / "split-manifest.snapshot.json")
            and manifest["split_manifest_id"] == data.manifest_id
            and manifest["data_manifest_id"] == data.manifest["input_manifest_id"]
            and manifest["feature_contract_version"] == data.manifest["feature_contract_version"], "Split metadata differs")
    require(consumer_digest(data, config["batch_size"]) == w["test_digest"], "Test tensor digest changed")
    model = ForecastAdapter.load(base / "checkpoint.pt", expected_contract=data.contract, runtime=config["runtime"])
    require(model.seed == manifest["seed"] and model.name == manifest["model_name"]
            and model.fit_record == upstream["wp6"]["training"], "Checkpoint identity/fit record mismatch")
    result = model.predict(data, batch_size=config["batch_size"])
    expected = prediction_frame(manifest["run_id"], data, result, manifest["seed"])
    saved = pd.read_csv(base / "predictions.csv", float_precision="round_trip", keep_default_na=False)
    pd.testing.assert_frame_equal(saved, expected, check_exact=True)
    metrics = load_json(base / "metrics.json")
    epsilon = config["metrics"]["smape_epsilon"]
    require(metrics == {"purpose": PURPOSE, "split": "test", "global": metrics_for(expected, epsilon),
                       "per_horizon": {str(h): metrics_for(g, epsilon) for h, g in expected.groupby("horizon_step")}},
            "Recomputed test metrics differ")
    categories, thresholds = context if context is not None else build_context(split_path, root, config)
    require(analyze(expected, categories, thresholds, config) == load_json(base / "error-analysis.json"), "Recomputed error analysis differs")
    worst = pd.read_csv(base / "worst-errors.csv", float_precision="round_trip", keep_default_na=False)
    pd.testing.assert_frame_equal(worst, expected.sort_values("absolute_error", ascending=False, kind="stable")
                                  .head(config["error_analysis"]["worst_error_rows"]).reset_index(drop=True), check_exact=True)
    timing = w["inference_benchmark"]
    require(timing["sample_count"] == len(data) and timing["split"] == "test"
            and all(timing.get(k) == v for k, v in config["inference_benchmark"].items()), "Inference benchmark policy changed")
    seconds = timing["seconds"]
    require(len(seconds) == timing["repetitions"] and all(np.isfinite(v) and v >= 0 for v in seconds), "Invalid inference timings")
    require(statistics.median(seconds) == timing["median_seconds"] == manifest["timing"]["inference_seconds"]
            and statistics.fmean(seconds) == timing["mean_seconds"], "Inference timing summary differs")
    require(w["upstream_training_seconds"] == upstream["timing"]["training_seconds"]
            and w["parameter_count"] == model.metadata()["parameter_count"], "Training/parameter provenance differs")
    return manifest


def verify(path, *, root=ROOT):
    root, path = Path(root).resolve(), Path(path).resolve()
    receipt = load_json(path)
    require(receipt["stage"] == "WP7_test_evaluation" and receipt["status"] == "complete", "Incomplete WP7 evaluation")
    config = load_json(path.parent / "config.resolved.json")
    validate_config(config)
    _, _, split_path = prepare_inputs(config, root)
    require(receipt["cohort_path"] == config["cohort_path"] and receipt["cohort_sha256"] == config["cohort_sha256"]
            and receipt["split_manifest_path"] == split_path.relative_to(root).as_posix()
            and receipt["split_manifest_sha256"] == sha256_file(split_path)
            and receipt["test_used_for_fitting_or_selection"] is False, "Evaluation receipt binding differs")
    expected_files = {p.relative_to(path.parent).as_posix(): sha256_file(p) for p in path.parent.iterdir()
                      if p.is_file() and p != path}
    require(expected_files == receipt["checksums"], "Evaluation summary checksum mismatch")
    validate_grid(receipt["runs"], config)
    manifests = []
    for entry in receipt["runs"]:
        member = private_path(root, entry["manifest_path"])
        require(sha256_file(member) == entry["sha256"], "WP7 member manifest changed")
        manifest = verify_run(member, root=root)
        require((manifest["model_name"], manifest["seed"]) == (entry["model_name"], entry["seed"]), "Receipt member identity mismatch")
        require({key: value for key, value in manifest["effective_config"].items() if key not in RESOLVED_FIELDS} == config,
                "WP7 members use different evaluation configurations")
        manifests.append(manifest)
    require(len({m["wp7"]["test_digest"] for m in manifests}) == 1, "Models/seeds used different test data")
    require(receipt["test_digest"] == manifests[0]["wp7"]["test_digest"], "Evaluation test digest differs")
    per_seed, summary = summarize(manifests, root)
    for filename, expected in (("per-seed.csv", per_seed), ("model-summary.csv", summary)):
        saved = pd.read_csv(path.parent / filename, float_precision="round_trip")
        pd.testing.assert_frame_equal(saved, expected, check_exact=True)
    print("WP7 evaluation, nine final checkpoints, test predictions, metrics and error analysis verified.", flush=True)
    return receipt


def run(config_path, *, root=ROOT):
    root, config_path = Path(root).resolve(), Path(config_path).resolve()
    config = load_json(config_path)
    validate_config(config)
    _, members, split_path = prepare_inputs(config, root)
    categories, thresholds = build_context(split_path, root, config)
    source_paths = sorted((root / "src").rglob("*.py")) + [config_path, root / "requirements.txt",
                    root / "configs/run-manifest.schema.json", root / config["protocol_document"]]
    sources = {p.relative_to(root).as_posix(): sha256_file(p) for p in source_paths}
    data_by_model = {name: ModelData(split_path, name, "test") for name in config["models"]}
    require(len({consumer_digest(d, config["batch_size"]) for d in data_by_model.values()}) == 1,
            "Model test loaders differ")
    started = time.perf_counter()
    paths = [evaluate_one(config, path, manifest, split_path, data_by_model[manifest["model_name"]],
                          categories, thresholds, sources, root) for path, manifest in members]
    require(all(sha256_file(root / name) == digest for name, digest in sources.items()), "Source/config changed during WP7")
    manifests = [load_json(path) for path in paths]
    per_seed, summary = summarize(manifests, root)
    base = private_path(root, config["output_root"] + f"/wp7-evaluation_{datetime.now(timezone.utc):%Y%m%dt%H%M%Sz}_{uuid.uuid4().hex[:8]}")
    base.mkdir(parents=True, exist_ok=False)
    write_json(base / "config.resolved.json", config)
    write_csv(base / "per-seed.csv", per_seed)
    write_csv(base / "model-summary.csv", summary)
    receipt = {"stage": "WP7_test_evaluation", "status": "complete", "purpose": PURPOSE,
               "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "cohort_path": config["cohort_path"], "cohort_sha256": config["cohort_sha256"],
               "split_manifest_path": split_path.relative_to(root).as_posix(), "split_manifest_sha256": sha256_file(split_path),
               "test_digest": manifests[0]["wp7"]["test_digest"], "execution_seconds": time.perf_counter() - started,
               "test_used_for_fitting_or_selection": False,
               "runs": [{"model_name": m["model_name"], "seed": m["seed"], "manifest_path": p.relative_to(root).as_posix(),
                         "sha256": sha256_file(p)} for p, m in zip(paths, manifests)],
               "checksums": {p.name: sha256_file(p) for p in base.iterdir() if p.is_file()}}
    destination = base / "evaluation.json"
    write_json(destination, receipt)
    verify(destination, root=root)
    print(f"WP7 completed: {destination.relative_to(root).as_posix()}", flush=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.config:
        run(args.config)
    else:
        verify(args.verify)


if __name__ == "__main__":
    main()
