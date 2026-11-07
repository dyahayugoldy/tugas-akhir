"""WP4: frozen chronological splits, train-only transforms and shared windows.

Evaluation is rolling origin. Earlier holdout actuals may enter later inputs
only after availability; no label from a sample's horizon enters its inputs.
This stage uses NumPy; framework-specific model adapters belong to WP5.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import time
import uuid
import zipfile

import numpy as np
import pandas as pd

from src.data.harmonize_workbooks import ROOT, load_json, relative_display, relative_path, sha256_file
from src.data.weekly_panel import complete_calendar, save_csv
from src.features.calendar_lags import build_features, features_at_origin
from src.utils.manifest import collect_environment, source_info, write_json


SPLITS = ("train", "validation", "test")
MODELS = ("moebs_transformer", "nspace_transformer", "tft")
INDEX_COLUMNS = ["sample_id", "split", "series_id", "origin_time", "encoder_start", "encoder_end",
                 "target_start", "target_end", "target_available_at", "max_input_available_at"]


def validate_config(config, contract, panel_config):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", config["config_version"]):
        raise ValueError("Invalid WP4 config version")
    if type(config["seed"]) is not int or config["seed"] < 0 or config["models"] != list(MODELS):
        raise ValueError("WP4 requires one seed and the three common model consumers")
    supported = {
        "calendar_policy": "whole_monday_sunday_weeks_contiguous_target_ranges",
        "evaluation": "rolling_origin_observed_history_frozen_preprocessing",
        "history": "within_experiment_start_prior_splits_allowed_if_available_at_origin",
    }
    if any(config[key] != expected for key, expected in supported.items()):
        raise ValueError("Unsupported split or evaluation policy")
    if set(config["split"]) != set(SPLITS):
        raise ValueError("Exactly three chronological split intervals are required")
    previous = None
    calendar = complete_calendar(panel_config)
    for name in SPLITS:
        part = config["split"][name]
        start, end = pd.Timestamp(part["start"]), pd.Timestamp(part["end"])
        if start.dayofweek != 0 or end.dayofweek != 6 or start > end:
            raise ValueError("Split intervals must contain complete Monday-Sunday weeks")
        if start < calendar.min() or end > calendar.max() + pd.Timedelta(days=6):
            raise ValueError("Split interval exceeds complete source coverage")
        if previous is not None and start != previous + pd.Timedelta(days=1):
            raise ValueError("Split intervals must be chronological and contiguous without overlap")
        previous = end
    design = contract["window_design"]
    if not (config["split"]["train"]["start"] <= design["development_start"] and
            config["split"]["train"]["end"] >= design["development_end"]):
        raise ValueError("WP3 window design period must be contained in train")
    window = config["window"]
    for key in ("encoder_weeks", "horizon_weeks", "stride_weeks"):
        if type(window[key]) is not int or window[key] <= 0:
            raise ValueError("Window lengths and stride must be positive integers")
    if any(window[key] != design[key] for key in ("encoder_weeks", "horizon_weeks")):
        raise ValueError("WP4 must preserve the frozen WP3 encoder and horizon")
    if not window["require_complete_encoder_lags"] or window["cold_start"] != "exclude_sample_until_history_available_report_per_split":
        raise ValueError("Unsupported encoder history policy")
    expected = {
        "target_scaler": "global_standard_train_unique_panel_rows_ddof0",
        "lag_scaler": "shared_target_scaler",
        "calendar_scaler": "per_field_standard_train_unique_panel_rows_ddof0",
        "unavailable_lag": "scaled_zero_with_false_mask_no_statistical_imputation",
        "numeric_dtype": "float32", "categorical_dtype": "int64",
        "feature_selection": "fixed_WP3_contract_no_data_driven_selection",
    }
    prep = config["preprocessing"]
    if any(prep[key] != value for key, value in expected.items()):
        raise ValueError("Unsupported preprocessing policy")
    if prep["unknown_token"] != contract["country_missing"]["unseen_category_encoder_token"] or prep["unknown_id"] != 0:
        raise ValueError("Unknown category encoding differs from the feature contract")
    if not np.isfinite(prep["constant_scale"]) or prep["constant_scale"] <= 0:
        raise ValueError("Constant-feature scale must be positive and finite")
    loader = config["loader"]
    if type(loader["batch_size"]) is not int or loader["batch_size"] <= 0 or loader["shuffle"] or loader["drop_last"]:
        raise ValueError("WP4 contract loaders must retain ordered complete samples")
    groups = contract["feature_groups"]
    if (groups["static_categorical"] != contract["series"]["fields"] or
            groups["known_future"] != panel_config["features"]["calendar_fields"] or
            groups["observed_past"] != [f"target_lag_{lag}" for lag in panel_config["features"]["target_lags_weeks"]]):
        raise ValueError("WP3 feature groups do not match the approved configuration")


def assign_splits(panel, config):
    result = panel[["series_id", "week_start", "week_end", "available_at"]].copy()
    result["split"] = "excluded"
    for name in SPLITS:
        interval = config["split"][name]
        selected = panel.week_start.ge(interval["start"]) & panel.week_end.le(interval["end"])
        result.loc[selected, "split"] = name
    return result


def fit_preprocessing(panel, features, registry, config, contract):
    """Fit once on unique train rows, never repeated windows or holdout rows."""
    parts = assign_splits(panel, config)
    cutoff = pd.Timestamp(config["split"]["train"]["end"]) + pd.Timedelta(days=1)
    fit = parts["split"].eq("train") & pd.to_datetime(panel.available_at).le(cutoff)
    if not fit.any():
        raise ValueError("No train rows available for preprocessing")
    target = contract["target"]["field"]
    constant = config["preprocessing"]["constant_scale"]

    def standard(values):
        numeric = np.asarray(values, dtype=np.float64)
        if not np.isfinite(numeric).all():
            raise ValueError("Nonfinite preprocessing input; inspect private data artifacts")
        scale = float(numeric.std(ddof=0))
        return {"mean": float(numeric.mean()), "scale": scale if scale > 0 else constant,
                "constant": scale == 0, "count": len(numeric)}

    known = contract["feature_groups"]["known_future"]
    ids = set(panel.loc[fit, "series_id"])
    categories = registry.loc[registry.series_id.isin(ids) & pd.to_datetime(registry.known_at).le(cutoff)]
    vocabularies = {}
    token = config["preprocessing"]["unknown_token"]
    for field in contract["feature_groups"]["static_categorical"]:
        values = sorted(set(categories[field]))
        if token in values:
            raise ValueError("Reserved encoder token collides with a train category")
        vocabularies[field] = {token: 0, **{value: i + 1 for i, value in enumerate(values)}}
    return {"fit_split": "train", "fit_start": config["split"]["train"]["start"],
            "fit_end": config["split"]["train"]["end"], "fit_available_by": cutoff.strftime("%Y-%m-%d"),
            "fit_row_count": int(fit.sum()), "fit_max_week": panel.loc[fit, "week_start"].max(),
            "target_field": target, "target": standard(panel.loc[fit, target]),
            "calendar": {field: standard(features.loc[fit, field]) for field in known},
            "vocabularies": vocabularies, "unknown_token": token, "unknown_id": 0,
            "fit_panel_keys_sha256": hashlib.sha256(parts.loc[fit, ["series_id", "week_start"]].to_csv(index=False).encode()).hexdigest(),
            "statistical_imputation": "none", "feature_selection": config["preprocessing"]["feature_selection"]}


def inverse_target(values, preprocessing):
    scale = preprocessing["target"]
    return np.asarray(values, dtype=np.float64) * scale["scale"] + scale["mean"]


def validate_tables(panel, features, registry, contract, panel_config):
    """Rebuild deterministic features to reject altered lag sources or labels."""
    keys = ["series_id", "week_start"]
    if panel.duplicated(keys).any() or registry.series_id.duplicated().any():
        raise ValueError("Duplicate panel key or registry identity")
    if not set(panel.series_id).issubset(registry.series_id):
        raise ValueError("Panel identity missing from registry")
    target = contract["target"]["field"]
    try:
        values = panel[target].to_numpy(dtype=np.float64)
        weeks = pd.to_datetime(panel.week_start, format="%Y-%m-%d", errors="raise")
        ends = pd.to_datetime(panel.week_end, format="%Y-%m-%d", errors="raise")
        available = pd.to_datetime(panel.available_at, format="%Y-%m-%d", errors="raise")
        known = pd.to_datetime(registry.known_at, format="%Y-%m-%d", errors="raise")
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid private panel or registry values") from error
    delay = pd.Timedelta(days=panel_config["availability"]["capture_delay_days"])
    if (not np.isfinite(values).all() or (values < 0).any() or weeks.isna().any() or known.isna().any() or
            not weeks.dt.dayofweek.eq(0).all() or not ends.eq(weeks + pd.Timedelta(days=6)).all() or
            not available.eq(weeks + pd.Timedelta(weeks=1) + delay).all()):
        raise ValueError("Invalid target, complete week, or availability metadata")
    calendar = set(complete_calendar(panel_config).strftime("%Y-%m-%d"))
    if not set(panel.week_start).issubset(calendar):
        raise ValueError("Panel contains an incomplete source week")
    activation = panel.series_id.map(registry.set_index("series_id").activation_week)
    if panel.week_start.lt(activation).any():
        raise ValueError("Panel contains preactivation rows")
    expected = build_features(panel, target, panel_config["features"])
    actual = features.sort_values(keys).reset_index(drop=True)
    if list(expected.columns) != list(actual.columns):
        raise ValueError("Feature schema differs from the WP3 contract")
    try:
        # Numeric calendars were serialized to CSV; quantities and audit text remain exact.
        for column in expected:
            if column in panel_config["features"]["calendar_fields"]:
                np.testing.assert_allclose(expected[column].to_numpy(float), actual[column].to_numpy(float), rtol=1e-12, atol=1e-12)
            else:
                if not expected[column].astype(str).equals(actual[column].astype(str)):
                    raise AssertionError()
    except (AssertionError, ValueError) as error:
        raise ValueError("WP3 feature reconstruction mismatch; inspect private artifacts") from error


def build_windows(panel, features, registry, config, contract, panel_config):
    panel = panel.sort_values(["series_id", "week_start"]).reset_index(drop=True)
    features = features.sort_values(["series_id", "week_start"]).reset_index(drop=True)
    validate_config(config, contract, panel_config)
    validate_tables(panel, features, registry, contract, panel_config)
    parts = assign_splits(panel, config)
    prep = fit_preprocessing(panel, features, registry, config, contract)
    target = contract["target"]["field"]
    groups = contract["feature_groups"]
    lag_names, known_fields, static_fields = (groups[key] for key in ("observed_past", "known_future", "static_categorical"))
    lags = panel_config["features"]["target_lags_weeks"]
    enc, horizon = (config["window"][key] for key in ("encoder_weeks", "horizon_weeks"))
    y = panel[target].to_numpy(dtype=np.float64)
    target_scale = prep["target"]
    standardized_y = (y - target_scale["mean"]) / target_scale["scale"]
    lag_values = features[lag_names].where(features[lag_names].ne(""), np.nan).to_numpy(dtype=np.float64)
    lag_values = (lag_values - target_scale["mean"]) / target_scale["scale"]
    calendar_values = np.column_stack([(features[field].to_numpy(float) - prep["calendar"][field]["mean"]) /
                                       prep["calendar"][field]["scale"] for field in known_fields])
    availability = pd.to_datetime(panel.available_at).to_numpy()
    registry_lookup = registry.set_index("series_id")
    series_positions = {sid: g.index.to_numpy() for sid, g in panel.groupby("series_id", sort=True)}
    lookup = {(row.series_id, row.week_start): i for i, row in enumerate(panel.itertuples())}
    records, tensors = [], {name: [] for name in (
        "encoder_observed", "encoder_observed_mask", "encoder_known", "decoder_known",
        "decoder_lags", "decoder_lag_mask", "static_categorical", "y", "y_scaled")}
    counts = {name: Counter() for name in SPLITS}
    start, end = config["split"]["train"]["start"], config["split"]["test"]["end"]
    origins = pd.date_range(start, end, freq=f"{7 * config['window']['stride_weeks']}D")
    for origin in origins:
        date = origin.strftime("%Y-%m-%d")
        part = next((name for name in SPLITS if config["split"][name]["start"] <= date <= config["split"][name]["end"]), None)
        if part is None:
            continue
        lower = (origin - pd.Timedelta(weeks=enc)).strftime("%Y-%m-%d")
        upper = (origin + pd.Timedelta(weeks=horizon - 1)).strftime("%Y-%m-%d")
        subset = features.loc[features.week_start.between(lower, upper)]
        masked = features_at_origin(subset, date, lags, registry)
        masks = np.zeros((len(panel), len(lags)), dtype=bool)
        masks[masked.index] = masked[[f"{name}_observed" for name in lag_names]].eq("true").to_numpy()
        for sid, positions in series_positions.items():
            row = lookup.get((sid, date))
            if row is None:
                continue
            count = counts[part]
            count["candidate_origins"] += 1
            if pd.Timestamp(registry_lookup.loc[sid, "known_at"]) > origin:
                count["unknown_at_origin"] += 1
                continue
            index = int(np.searchsorted(positions, row))
            if index < enc or lower < start:
                count["insufficient_encoder_history"] += 1
                continue
            if index + horizon > len(positions):
                count["incomplete_target_horizon"] += 1
                continue
            e = positions[index - enc:index]
            d = positions[index:index + horizon]
            if not parts.loc[d, "split"].eq(part).all():
                count["target_crosses_split"] += 1
                continue
            if not (availability[e] <= origin.to_datetime64()).all():
                count["encoder_target_unavailable"] += 1
                continue
            if not masks[e].all():
                count["insufficient_encoder_lags"] += 1
                continue
            # Every observed source must precede the original forecast origin.
            source_weeks = features.loc[np.r_[e, d], [f"{name}_source_week" for name in lag_names]].to_numpy()
            active_masks = masks[np.r_[e, d]]
            if any(value >= date for value in source_weeks[active_masks]):
                raise ValueError("A horizon label entered observed inputs")
            observed = np.column_stack([standardized_y[e], lag_values[e]])
            decoder_lags = np.where(masks[d], lag_values[d], 0.0)
            category = registry_lookup.loc[sid]
            static = [prep["vocabularies"][field].get(category[field], 0) for field in static_fields]
            for field, value in zip(static_fields, static):
                count[f"unknown_{field}"] += value == 0
            input_times = [*panel.loc[e, "available_at"],
                           *features.loc[np.r_[e, d], [f"{name}_available_at" for name in lag_names]].to_numpy()[active_masks]]
            max_available = max(input_times)
            if max_available > date:
                raise ValueError("A future realized value entered a window")
            sample_id = hashlib.sha256(json.dumps([sid, date, enc, horizon], separators=(",", ":")).encode()).hexdigest()
            records.append(dict(zip(INDEX_COLUMNS, [sample_id, part, sid, date, panel.loc[e[0], "week_start"],
                panel.loc[e[-1], "week_end"], date, panel.loc[d[-1], "week_end"],
                panel.loc[d[-1], "available_at"], max_available])))
            values = {"encoder_observed": observed, "encoder_observed_mask": np.ones_like(observed, dtype=bool),
                      "encoder_known": calendar_values[e], "decoder_known": calendar_values[d],
                      "decoder_lags": decoder_lags, "decoder_lag_mask": masks[d], "static_categorical": static,
                      "y": y[d], "y_scaled": standardized_y[d]}
            for key, value in values.items():
                tensors[key].append(value)
            count["samples"] += 1
    index = pd.DataFrame(records, columns=INDEX_COLUMNS)
    if index.empty or any(not index.split.eq(name).any() for name in SPLITS):
        raise ValueError("Each split must contain eligible windows")
    arrays = {key: np.asarray(value, dtype=(bool if "mask" in key else np.int64 if key == "static_categorical" else np.float32))
              for key, value in tensors.items()}
    arrays["sample_id"] = index.sample_id.to_numpy(dtype="U64")
    arrays["series_id"] = index.series_id.to_numpy(dtype="U64")
    arrays["origin_time"] = index.origin_time.to_numpy(dtype="U10")
    if index.sample_id.duplicated().any() or any(not np.isfinite(value).all() for key, value in arrays.items() if value.dtype.kind == "f"):
        raise ValueError("Duplicate windows or nonfinite tensors")
    counts = {name: dict(counts[name]) for name in SPLITS}
    return index, parts, arrays, prep, counts


def save_tensors(path, arrays):
    """Deterministic NPZ members: fixed ZIP timestamps and no Python objects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(arrays.items()):
            buffer = io.BytesIO()
            np.lib.format.write_array(buffer, value, allow_pickle=False)
            member = zipfile.ZipInfo(name + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, buffer.getvalue())
    with np.load(path, allow_pickle=False) as saved:
        if set(saved.files) != set(arrays) or any(not np.array_equal(saved[key], value) for key, value in arrays.items()):
            raise ValueError("Tensor roundtrip mismatch in private run artifacts")


def load_inputs(config_path, root):
    config = load_json(config_path)
    resolve = lambda value: relative_path(root, value, description="WP4 input")
    pointer_path = resolve(config["current_data_pointer"])
    pointer = load_json(pointer_path)
    manifest_path = resolve(pointer[config["input_manifest_key"]])
    manifest = load_json(manifest_path)
    if manifest.get("stage") != "WP3_weekly_panel" or not manifest.get("ready_for_split") or manifest.get("final_split_created"):
        raise ValueError("WP4 requires a ready immutable WP3 panel manifest")
    monitored = {path: sha256_file(path) for path in (config_path, pointer_path, manifest_path)}
    for name, expected in manifest["outputs"].items():
        path = resolve(relative_display(root, manifest_path.parent / name))
        if not path.is_relative_to(manifest_path.parent) or sha256_file(path) != expected:
            raise ValueError("WP3 output checksum mismatch or escaped run directory")
        monitored[path] = expected
    # current-data.json is deliberately mutable after activation. Its immutable
    # snapshot is checked among WP3 outputs; every other input remains pinned.
    mutable_pointer = relative_display(root, pointer_path)
    for name, expected in manifest["inputs_sha256"].items():
        if name == mutable_pointer:
            continue
        path = resolve(name)
        if sha256_file(path) != expected:
            raise ValueError("WP3 ancestor or source checksum mismatch")
        monitored[path] = expected
    base = manifest_path.parent
    contract, panel_config = load_json(base / "feature_contract.json"), load_json(base / "config.resolved.json")
    validate_config(config, contract, panel_config)
    frame = lambda name: pd.read_csv(base / name, dtype=str, keep_default_na=False)
    panel, features, registry = (frame(name) for name in ("panel/panel_weekly.csv.gz", "features/calendar_lags.csv.gz", "panel/series_registry.csv.gz"))
    if len(panel) != manifest["counts"]["panel_rows"] or len(registry) != manifest["counts"]["observed_series"]:
        raise ValueError("WP3 row counts differ from manifest")
    return config, pointer, manifest_path, manifest, contract, panel_config, panel, features, registry, monitored


def run(config_path, *, root=ROOT):
    started = time.perf_counter()
    config_path = Path(config_path).resolve()
    config, pointer, parent_path, parent, contract, panel_config, panel, features, registry, monitored = load_inputs(config_path, root)
    output_root = relative_path(root, config["output_root"], description="WP4 output")
    if not output_root.is_relative_to((root / "artifacts/data").resolve()):
        raise ValueError("WP4 outputs must remain in private artifacts/data")
    source_names = ("src/features/split_windows.py", "src/features/window_dataset.py", "src/features/calendar_lags.py",
                    "src/data/weekly_panel.py", "src/data/harmonize_workbooks.py", "src/data/business_selection.py",
                    "src/utils/manifest.py")
    sources = {name: sha256_file(ROOT / name) for name in source_names}
    index, parts, arrays, prep, counts = build_windows(panel, features, registry, config, contract, panel_config)
    run_id = f"shared_{config['config_version']}_seed-{config['seed']}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"
    output = output_root / run_id
    output.mkdir(parents=True, exist_ok=False)
    save_csv(output / "index/window_index.csv.gz", index)
    save_csv(output / "index/panel_split.csv.gz", parts)
    for part in SPLITS:
        selected = index.split.eq(part).to_numpy()
        save_tensors(output / f"tensors/{part}.npz", {key: value[selected] for key, value in arrays.items()})
    schema = {"encoder_observed": [contract["target"]["field"], *contract["feature_groups"]["observed_past"]],
              "encoder_known": contract["feature_groups"]["known_future"], "decoder_known": contract["feature_groups"]["known_future"],
              "decoder_lags": contract["feature_groups"]["observed_past"], "static_categorical": contract["feature_groups"]["static_categorical"],
              "label_fields": ["y", "y_scaled"], "numeric_dtype": "float32", "categorical_dtype": "int64",
              "masks_dtype": "bool", "y_unit": contract["target"]["unit"],
              "missing_lag_fill": "0_in_scaled_space_only_when_mask_false", "series_id": "metadata_not_embedding_vocabulary",
              "shapes_excluding_sample_axis": {key: list(value.shape[1:]) for key, value in arrays.items()}}
    report = {"counts": counts, "panel_rows_by_split": parts.split.value_counts().to_dict(),
              "calendar_weeks_by_split": {name: int(parts.loc[parts.split.eq(name), "week_start"].nunique()) for name in SPLITS},
              "actual_target_ranges": {name: {"first": index.loc[index.split.eq(name), "target_start"].min(),
                                               "last": index.loc[index.split.eq(name), "target_end"].max()} for name in SPLITS},
              "preprocessing_fit_rows": prep["fit_row_count"], "evaluation": config["evaluation"],
              "availability_assumption": parent["availability_assumption"], "coverage_assumption": parent["coverage_assumption"],
              "decisions": config["decisions"], "model_training_performed": False}
    for name, value in (("config.resolved.json", config), ("preprocessing.json", prep), ("tensor_schema.json", schema),
                        ("feature_contract.json", contract), ("split_report.json", report), ("current-data.snapshot.json", pointer)):
        write_json(output / name, value)
    for name, expected in sources.items():
        path = output / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
        if sha256_file(path) != expected:
            raise ValueError("Source changed during WP4 snapshot")
    for path, expected in monitored.items():
        if sha256_file(path) != expected:
            raise ValueError("A monitored WP4 input changed during processing")
    revision, dirty = source_info(ROOT)
    manifest = {"manifest_id": run_id, "stage": "WP4_split_windows", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "config_version": config["config_version"], "seed": config["seed"],
                "seed_usage": "deterministic_build_seed_reserved_for_reproducible_loader_shuffling",
                "input_manifest_id": parent["manifest_id"], "input_manifest": relative_display(root, parent_path),
                "input_manifest_sha256": sha256_file(parent_path), "feature_contract_version": contract["contract_version"],
                "split": config["split"], "evaluation": config["evaluation"], "window": config["window"],
                "shared_models": config["models"], "counts": counts, "source_commit": revision, "source_dirty": dirty,
                "source_code_sha256": sources, "environment": collect_environment(),
                "inputs_sha256": {relative_display(root, path): expected for path, expected in monitored.items()},
                "outputs": {relative_display(output, path): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file()},
                "availability_assumption": parent["availability_assumption"], "coverage_assumption": parent["coverage_assumption"],
                "ready_for_models": False, "verification": {"inputs_unchanged": True, "tensor_roundtrip_exact": True,
                    "train_only_preprocessing": True, "origin_availability_checked": True,
                    "horizon_labels_excluded_from_own_inputs": True, "three_consumer_contract": False},
                "privacy": {"safe_to_commit": False}, "elapsed_seconds": round(time.perf_counter() - started, 3)}
    # Validate real serialized data through each common consumer before sealing.
    from src.features.window_dataset import verify_consumers
    manifest_path = output / "split_manifest.json"
    verification = verify_consumers(output, manifest)
    write_json(output / "loader_verification.json", verification)
    manifest["outputs"]["loader_verification.json"] = sha256_file(output / "loader_verification.json")
    manifest["verification"]["three_consumer_contract"] = True
    if any(sha256_file(path) != expected for path, expected in monitored.items()):
        raise ValueError("A monitored WP4 input changed before sealing the manifest")
    if any(sha256_file(ROOT / name) != expected for name, expected in sources.items()):
        raise ValueError("WP4 source code changed during processing")
    manifest["ready_for_models"] = True
    manifest["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(manifest_path, manifest)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    output = run(args.config)
    summary = load_json(output / "split_report.json")
    print(f"WP4 completed: {output.relative_to(ROOT).as_posix()}")
    print(json.dumps({name: value["samples"] for name, value in summary["counts"].items()}))


if __name__ == "__main__":
    main()
