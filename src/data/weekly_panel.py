"""WP3: reproducible weekly labels, descriptive EDA, and causal features.

The input is one immutable WP2 manifest. Outputs stay private under artifacts;
no vocabulary, scaler, statistical imputer, model, or final split is fitted.
Annual coverage and capture latency are explicit assumptions, not inferred
from an absence of transactions. Incomplete boundary weeks remain in audit.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.business_selection import exclusion_reasons, write_json
from src.data.harmonize_workbooks import ROOT, load_json, relative_display, relative_path, sha256_file
from src.features.calendar_lags import CALENDAR_FIELDS, build_features


def decimal_text(value: Decimal) -> str:
    """Render exact quantities without Decimal.normalize's context rounding."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if value == 0 else rendered


def exact_sum(values, precision: int) -> Decimal:
    with localcontext() as context:
        context.prec = precision
        # Never silently accept arithmetic rounding in the target pipeline.
        from decimal import Inexact
        context.traps[Inexact] = True
        return sum(values, Decimal(0))


def validate_config(config: dict, contract: dict) -> None:
    if contract["series"]["status"] != "business_confirmed":
        raise ValueError("Series definition requires business confirmation")
    fields = contract["series"]["fields"]
    classes = {column["field"]: column["class"] for column in contract["columns"]}
    if not fields or len(fields) != len(set(fields)) or any(classes.get(f) != "static_categorical" for f in fields):
        raise ValueError("Series keys must be distinct approved static categorical fields")
    mapping = contract["series"]["brand_mapping"]
    if not mapping or mapping["canonical"] not in fields or mapping["status"] != "business_confirmed":
        raise ValueError("Missing confirmed brand mapping")
    if contract["time"]["week_frequency"] != "W-SUN" or contract["time"]["week_start"] != "Monday":
        raise ValueError("Supported weekly calendar is Monday through Sunday")
    if contract["target"]["unit_status"] != "business_confirmed" or contract["target"]["aggregation"] != "sum":
        raise ValueError("Target unit and sum aggregation must be confirmed")
    calendar = contract["calendar_regularization"]
    if calendar["no_transaction_week"] != "zero_demand" or calendar["status"] != "business_confirmed":
        raise ValueError("Zero demand regularization requires business confirmation")
    if Decimal(calendar["target_fill_value"]) != 0 or calendar["unit"] != contract["target"]["unit"]:
        raise ValueError("Calendar fill must be zero in the target unit")
    periods = config["coverage"]["periods"]
    if not periods or len({period["source_year"] for period in periods}) != len(periods):
        raise ValueError("Declared source coverage is required")
    previous_end = None
    for period in periods:
        start, end = pd.Timestamp(period["start"]), pd.Timestamp(period["end"])
        if start > end or start.year != period["source_year"] or end.year != period["source_year"]:
            raise ValueError("Coverage must have ordered dates within its source year")
        if previous_end is not None and start != previous_end + pd.Timedelta(days=1):
            raise ValueError("Coverage intervals must be contiguous and non-overlapping")
        previous_end = end
    supported = [
        (config["coverage"], "partial_weeks", "retain_observed_aggregate_in_audit_exclude_from_panel"),
        (config["coverage"], "gaps", "reject_noncontiguous_declared_coverage"),
        (config["series_activity"], "start", "first_observed_transaction_week"),
        (config["series_activity"], "before_start", "do_not_create_rows"),
        (config["series_activity"], "end", "last_complete_week_in_declared_coverage"),
        (config["series_activity"], "inactivity", "retain_zero_weeks_never_infer_closure_from_last_transaction"),
        (config["availability"], "mode", "retrospective_event_time_proxy"),
        (config["features"], "lag_missing", "retain_null_with_availability_mask_never_backfill"),
        (config["window_design"], "selection_rule", "largest_encoder_retaining_minimum_fraction_of_shortest_encoder_development_windows"),
    ]
    if any(obj[key] != value for obj, key, value in supported):
        raise ValueError("Unsupported panel policy")
    settings = config["features"]
    lags = settings["target_lags_weeks"]
    candidates = config["window_design"]["encoder_candidates_weeks"]
    for values in [lags, candidates]:
        if not values or values != sorted(set(values)) or any(type(v) is not int or v <= 0 for v in values):
            raise ValueError("Lag and encoder lengths must be sorted distinct positive integers")
    delay = config["availability"]["capture_delay_days"]
    if type(delay) is not int or delay < 0:
        raise ValueError("Capture delay must be a nonnegative integer")
    if not set(settings["calendar_fields"]).issubset(CALENDAR_FIELDS) or len(settings["calendar_fields"]) != len(set(settings["calendar_fields"])):
        raise ValueError("Unsupported or duplicate calendar fields")
    if min(settings["week_cycle_period"], settings["month_cycle_period"]) <= 0 or settings["tensor_dtype"] != "float32":
        raise ValueError("Invalid calendar cycle or tensor dtype")
    design = config["window_design"]
    start, end = pd.Timestamp(design["development_start"]), pd.Timestamp(design["development_end"])
    if start > end or start.dayofweek != 0 or end.dayofweek != 6:
        raise ValueError("Development period must cover ordered complete Monday-Sunday weeks")
    if start < pd.Timestamp(periods[0]["start"]) or end > pd.Timestamp(periods[-1]["end"]):
        raise ValueError("Development period must be within declared coverage")
    if type(design["horizon_weeks"]) is not int or design["horizon_weeks"] <= 0:
        raise ValueError("Horizon must be a positive integer")
    if not 0 < design["minimum_window_retention"] <= 1 or not design["require_complete_encoder_lags"]:
        raise ValueError("Unsupported window retention or missing-lag policy")
    if not set(config["eda"]["group_fields"]).issubset(classes):
        raise ValueError("EDA grouping must use declared source columns")
    if type(config["decimal_precision"]) is not int or config["decimal_precision"] < 28:
        raise ValueError("Target precision must be at least the canonical decimal precision")


def complete_calendar(config: dict) -> pd.DatetimeIndex:
    periods = config["coverage"]["periods"]
    start, end = pd.Timestamp(periods[0]["start"]), pd.Timestamp(periods[-1]["end"])
    start = start + pd.Timedelta(days=(-start.dayofweek) % 7)
    calendar = pd.date_range(start, end, freq="7D")
    calendar = calendar[calendar + pd.Timedelta(days=6) <= end]
    if calendar.empty:
        raise ValueError("No complete week in declared coverage")
    return calendar


def load_inputs(root: Path, config_path: Path, config: dict):
    resolve = lambda path: relative_path(root, path, description="WP3 input")
    pointer_path = resolve(config["current_data_pointer"])
    pointer = load_json(pointer_path)
    manifest_path = resolve(pointer[config["input_manifest_key"]])
    manifest = load_json(manifest_path)
    if manifest.get("stage") != "WP2_business_selection" or not manifest["ready_for_panel"] or manifest["blockers"]:
        raise ValueError("WP3 requires a ready WP2 business-selection manifest")
    contract_path = resolve(config["feature_contract"])
    contract = load_json(contract_path)
    validate_config(config, contract)
    monitored = {path: sha256_file(path) for path in [config_path, pointer_path, manifest_path, contract_path]}
    for name, expected_hash in manifest["outputs"].items():
        path = resolve(relative_display(root, manifest_path.parent / name))
        if not path.is_relative_to(manifest_path.parent) or sha256_file(path) != expected_hash:
            raise ValueError("WP2 output checksum mismatch or escaped run directory")
        monitored[path] = expected_hash
    if load_json(manifest_path.parent / "feature_contract.json") != contract:
        raise ValueError("Active contract differs from the immutable WP2 contract")
    selection = load_json(manifest_path.parent / "config.resolved.json")
    canonical_path = resolve(manifest["input_manifest"])
    if sha256_file(canonical_path) != manifest["inputs_sha256"][manifest["input_manifest"]]:
        raise ValueError("WP1 ancestor manifest checksum mismatch")
    canonical = load_json(canonical_path)
    cleaning_path = resolve(canonical["input_cleaning_manifest"])
    if sha256_file(cleaning_path) != canonical["input_cleaning_manifest_sha256"]:
        raise ValueError("Cleaning ancestor manifest checksum mismatch")
    sources = load_json(cleaning_path)["config"]["sources"]
    lookup = {Path(source).name: resolve(source) for source in sources}
    monitored.update({canonical_path: sha256_file(canonical_path), cleaning_path: sha256_file(cleaning_path)})
    for source in canonical["sources"]:
        path = lookup[source["source_file"]]
        if sha256_file(path) != source["source_sha256"]:
            raise ValueError("Raw source checksum mismatch")
        monitored[path] = source["source_sha256"]
    input_path = manifest_path.parent / "selected_transactions.csv.gz"
    frame = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    required = [column["field"] for column in contract["columns"]] + ["source_year", "wp2_quality_holds"]
    if len(frame) != manifest["row_count"] or not set(required).issubset(frame.columns):
        raise ValueError("WP2 transactions differ from the source contract or row count")
    if frame["wp2_quality_holds"].str.strip().ne("").any():
        raise ValueError("Unresolved transaction quality holds prohibit panel construction")
    if any(exclusion_reasons(row, selection) for row in frame.to_dict("records")):
        raise ValueError("A forbidden business-filter row remains in the WP2 input")
    return frame, contract, pointer, manifest_path, manifest, monitored


def prepare_transactions(frame: pd.DataFrame, contract: dict, config: dict) -> pd.DataFrame:
    result = frame.copy()
    target, date = contract["target"]["field"], contract["time"]["primary"]
    try:
        result["_date"] = pd.to_datetime(result[date], format="%Y-%m-%d", errors="raise")
        result["_value"] = result[target].map(Decimal)
    except (ValueError, InvalidOperation) as error:
        raise ValueError("Invalid date or target cannot be regularized as zero") from error
    if result["_date"].isna().any() or not result["_value"].map(lambda value: value.is_finite() and value >= 0).all():
        raise ValueError("Missing, nonfinite, or negative targets require quality review")
    fields = contract["series"]["fields"]
    for field in fields:
        if result[field].str.strip().eq("").any() or not result[field].eq(result[field].str.strip().str.upper()).all():
            raise ValueError("Series categories must be nonblank and already canonically normalized")
    tuples = list(result[fields].itertuples(index=False, name=None))
    ids = {key: hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest() for key in set(tuples)}
    if len(set(ids.values())) != len(ids):
        raise AssertionError("Series identity digest collision")
    result["series_id"] = [ids[key] for key in tuples]
    result["_week"] = result["_date"].dt.to_period(contract["time"]["week_frequency"]).dt.start_time
    periods = {str(period["source_year"]): period for period in config["coverage"]["periods"]}
    if set(result["source_year"]) != set(periods):
        raise ValueError("Observed source years do not match declared coverage")
    for year, group in result.groupby("source_year"):
        period = periods[year]
        if not group["_date"].between(period["start"], period["end"]).all():
            raise ValueError("A KP date falls outside its declared source coverage")
    return result


def build_panel(frame: pd.DataFrame, contract: dict, config: dict):
    """Retain observed aggregates separately, then complete each active calendar."""
    target, fields = contract["target"]["field"], contract["series"]["fields"]
    calendar = complete_calendar(config)
    allowed_weeks = set(calendar)
    precision = config["decimal_precision"]
    observed_records, lookup = [], {}
    for (series, week), group in frame.groupby(["series_id", "_week"], sort=True):
        value = exact_sum(group["_value"], precision)
        record = {"series_id": series, "week_start": week.strftime("%Y-%m-%d"), target: decimal_text(value),
                  "transaction_count": len(group), "complete_coverage_week": "true" if week in allowed_weeks else "false"}
        observed_records.append(record)
        lookup[(series, week)] = (value, len(group))
    observed = pd.DataFrame(observed_records)
    registry_records, panel_records = [], []
    delay = pd.Timedelta(days=config["availability"]["capture_delay_days"])
    time_indices = {week: index for index, week in enumerate(calendar)}
    for series, group in frame.groupby("series_id", sort=True):
        first = group["_date"].min()
        activation_week = group["_week"].min()
        registry_records.append({"series_id": series, **{field: group.iloc[0][field] for field in fields},
                                 "known_at": (first + delay).strftime("%Y-%m-%d"),
                                 "activation_week": activation_week.strftime("%Y-%m-%d")})
        # No last-transaction cutoff or minimum full-period length filter.
        for week in calendar[calendar >= activation_week]:
            value, count = lookup.get((series, week), (Decimal(0), 0))
            panel_records.append({"series_id": series, "week_start": week.strftime("%Y-%m-%d"),
                                  "week_end": (week + pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
                                  "time_idx": time_indices[week], target: decimal_text(value),
                                  "available_at": (week + pd.Timedelta(weeks=1) + delay).strftime("%Y-%m-%d"),
                                  "transaction_count": count,
                                  contract["calendar_regularization"]["zero_fill_indicator"]: "true" if count == 0 else "false"})
    if not panel_records:
        raise ValueError("No observed series has a complete panel week")
    panel = pd.DataFrame(panel_records)
    registry = pd.DataFrame(registry_records)
    if panel.duplicated(["series_id", "week_start"]).any():
        raise AssertionError("Duplicate weekly panel key")
    complete_rows = frame[frame["_week"].isin(allowed_weeks)]
    if exact_sum(panel[target].map(Decimal), precision) != exact_sum(complete_rows["_value"], precision):
        raise AssertionError("Weekly aggregation changed the accepted target total")
    if exact_sum(observed[target].map(Decimal), precision) != exact_sum(frame["_value"], precision):
        raise AssertionError("Observed aggregation changed the source target total")
    return observed, panel, registry


def choose_window_design(panel: pd.DataFrame, config: dict) -> dict:
    """Select input length from proposed-train support, never holdout targets."""
    design = config["window_design"]
    development = panel.loc[panel["week_start"].ge(design["development_start"]) & panel["week_end"].le(design["development_end"])].copy()
    horizon = design["horizon_weeks"]
    lag_context = max(config["features"]["target_lags_weeks"])
    candidates = []
    for encoder in design["encoder_candidates_weeks"]:
        windows = 0
        supporting_series = 0
        for _, group in development.groupby("series_id", sort=False):
            group = group.sort_values("week_start").reset_index(drop=True)
            count = 0
            for index in range(encoder + lag_context, len(group) - horizon + 1):
                origin = pd.Timestamp(group.loc[index, "week_start"])
                if pd.Timestamp(group.loc[index - 1, "available_at"]) <= origin:
                    count += 1
            windows += count
            supporting_series += count > 0
        candidates.append({"encoder_weeks": encoder, "development_windows": windows, "supporting_series": supporting_series})
    baseline = candidates[0]["development_windows"]
    if baseline == 0:
        raise ValueError("Development period has no eligible windows; revise the design configuration")
    for candidate in candidates:
        candidate["retained_window_fraction"] = candidate["development_windows"] / baseline
    eligible = [c for c in candidates if c["retained_window_fraction"] >= design["minimum_window_retention"]]
    chosen = eligible[-1]
    return {**copy.deepcopy(design), "candidates": candidates, "encoder_weeks": chosen["encoder_weeks"],
            "minimum_history_weeks": chosen["encoder_weeks"] + lag_context,
            "minimum_complete_series_weeks_for_one_window": chosen["encoder_weeks"] + lag_context + horizon,
            "selected_development_windows": chosen["development_windows"],
            "selection_uses_holdout": False, "final_split_created": False,
            "per_origin_eligibility_required": True}


def descriptive_eda(frame: pd.DataFrame, observed: pd.DataFrame, panel: pd.DataFrame, registry: pd.DataFrame, contract: dict, config: dict):
    precision, target = config["decimal_precision"], contract["target"]["field"]
    def grouped(keys):
        records = []
        for key, group in frame.groupby(keys, sort=True, dropna=False):
            key = key if isinstance(key, tuple) else (key,)
            records.append({**dict(zip(keys, [str(value) for value in key])), "transactions": len(group),
                            "quantity_pcs": decimal_text(exact_sum(group["_value"], precision))})
        return records
    frame = frame.copy()
    frame["month"] = frame["_date"].dt.strftime("%Y-%m")
    frame["week_start"] = frame["_week"].dt.strftime("%Y-%m-%d")
    grouped_eda = {"monthly": grouped(["month"]), "weekly": grouped(["week_start"]),
                   "by_category": {field: grouped([field]) for field in config["eda"]["group_fields"]}}
    source_fields = [column["field"] for column in contract["columns"]]
    coverage = []
    for period in config["coverage"]["periods"]:
        group = frame.loc[frame["source_year"].eq(str(period["source_year"]))]
        expected_months = pd.period_range(period["start"], period["end"], freq="M").astype(str)
        coverage.append({**period, "transactions": len(group), "observed_min": group["_date"].min().strftime("%Y-%m-%d"),
                         "observed_max": group["_date"].max().strftime("%Y-%m-%d"),
                         "months_without_transactions": sorted(set(expected_months) - set(group["month"]))})
    lengths = panel.groupby("series_id").size()
    zero_indicator = contract["calendar_regularization"]["zero_fill_indicator"]
    zero_filled = int(panel[zero_indicator].eq("true").sum())
    complete_observed = observed.loc[observed["complete_coverage_week"].eq("true")]
    partial = observed.loc[observed["complete_coverage_week"].eq("false")]
    counts = {
        "input_transactions": len(frame), "observed_series": len(registry), "panel_series": len(lengths),
        "complete_calendar_weeks": len(complete_calendar(config)), "panel_rows": len(panel),
        "observed_series_weeks_before_regularization": len(complete_observed), "zero_filled_rows": zero_filled,
        "zero_filled_fraction": zero_filled / len(panel),
        "zero_fraction_before_regularization": float(complete_observed[target].map(Decimal).eq(0).mean()),
        "zero_fraction_after_regularization": float(panel[target].map(Decimal).eq(0).mean()),
        "absent_active_series_weeks_before_regularization": len(panel) - len(complete_observed),
        "absent_active_series_weeks_after_regularization": 0, "duplicate_series_weeks": 0,
        "partial_week_series_rows_held_out": len(partial), "partial_week_transactions_held_out": int(partial["transaction_count"].sum()),
        "panel_transactions": int(panel["transaction_count"].sum()),
        "minimum_series_weeks": int(lengths.min()), "maximum_series_weeks": int(lengths.max()),
        "source_missing_target": int(frame[contract["target"]["field"]].eq("").sum()),
        "fractional_input_quantities": int(frame["_value"].map(lambda value: value % 1 != 0).sum()),
        "fractional_panel_quantities": int(panel[target].map(lambda value: Decimal(value) % 1 != 0).sum()),
    }
    if counts["panel_transactions"] + counts["partial_week_transactions_held_out"] != len(frame):
        raise AssertionError("Panel and partial-week audit do not partition source transactions")
    eda = {"scope": config["eda"]["scope"], "counts": counts, "coverage_audit": coverage,
           "coverage_completeness_status": config["coverage"]["completeness_status"],
           "missingness": {field: int(frame[field].str.strip().eq("").sum()) for field in source_fields},
           "series_length_quantiles": {str(q): float(lengths.quantile(q)) for q in config["eda"]["quantiles"]},
           "target_reconciliation": {"input_pcs": decimal_text(exact_sum(frame["_value"], precision)),
                                     "complete_week_panel_pcs": decimal_text(exact_sum(panel[target].map(Decimal), precision)),
                                     "partial_week_pcs": decimal_text(exact_sum(partial[target].map(Decimal), precision))},
           **grouped_eda}
    return eda


def save_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text_frame = frame.fillna("").astype(str)
    text_frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    try:
        pd.testing.assert_frame_equal(text_frame.reset_index(drop=True), saved)
    except AssertionError:
        # Pandas' detailed assertion can expose private category/target values.
        raise ValueError("WP3 CSV roundtrip mismatch; inspect the private run artifacts") from None


def run(config_path: Path, *, root: Path = ROOT) -> Path:
    started = time.perf_counter()
    root, config_path = root.resolve(), config_path.resolve()
    source_files = {"src/data/weekly_panel.py": Path(__file__),
                    "src/features/calendar_lags.py": Path(build_features.__code__.co_filename),
                    "src/data/business_selection.py": Path(write_json.__code__.co_filename),
                    "src/data/harmonize_workbooks.py": Path(load_json.__code__.co_filename)}
    source_hashes = {name: sha256_file(path) for name, path in source_files.items()}
    config = load_json(config_path)
    frame, contract, pointer, manifest_path, manifest, monitored = load_inputs(root, config_path, config)
    output_root = relative_path(root, config["output_root"], description="WP3 output")
    if not output_root.is_relative_to(root / "artifacts"):
        raise ValueError("Detailed WP3 outputs must remain under private artifacts")
    prepared = prepare_transactions(frame, contract, config)
    observed, panel, registry = build_panel(prepared, contract, config)
    features = build_features(panel, contract["target"]["field"], config["features"])
    design = choose_window_design(panel, config)
    eda = descriptive_eda(prepared, observed, panel, registry, contract, config)
    lengths = panel.groupby("series_id").size()
    eda["counts"]["short_panel_series_retained"] = int(lengths.lt(design["minimum_complete_series_weeks_for_one_window"]).sum())
    eda["counts"]["series_first_seen_after_development"] = int(registry["known_at"].gt(config["window_design"]["development_end"]).sum())
    feature_contract = copy.deepcopy(contract)
    feature_contract["parent_contract_version"] = contract["contract_version"]
    feature_contract["contract_version"] = f"{config['config_version']}-features"
    feature_contract["calendar_regularization"].update({"implementation_status": "implemented", "series_activity": config["series_activity"]})
    feature_contract["availability_assumption"] = config["availability"]
    feature_contract["feature_table_columns"] = list(features.columns)
    lag_names = [f"target_lag_{lag}" for lag in config["features"]["target_lags_weeks"]]
    feature_contract["feature_groups"] = {
        "static_categorical": contract["series"]["fields"],
        "known_future": config["features"]["calendar_fields"],
        "observed_past": lag_names,
        "availability_masks": [f"{name}_observed" for name in lag_names],
        "index_only": ["series_id", "week_start", "origin_time"],
        "audit_only": [f"{name}_{suffix}" for name in lag_names for suffix in ["source_week", "available_at"]],
        "labels_in_separate_panel": [contract["target"]["field"]],
    }
    feature_contract["window_design"] = design
    feature_contract["derived_features"] = [
        {"name": "calendar", "class": "known_future", "fields": config["features"]["calendar_fields"], "availability": "derive_from_target_week_only"},
        {"name": "target_lags", "class": "observed_past", "lags_weeks": config["features"]["target_lags_weeks"],
         "availability": "source_available_at_le_forecast_origin", "status": "implemented",
         "multi_step_rule": "call_features_at_origin_with_original_origin_and_gate_registry_known_at"},
    ]
    audit = {"counts": eda["counts"], "window_design": design,
             "target_sum_preserved_exactly": True, "input_quality_holds": 0,
             "series_policy_is_causal": True, "lag_source_and_availability_checks_passed": True,
             "lag_masks": {str(lag): {"available": int(features[f"target_lag_{lag}_observed"].eq("true").sum()),
                                      "unavailable": int(features[f"target_lag_{lag}_observed"].eq("false").sum())}
                           for lag in config["features"]["target_lags_weeks"]},
             "no_scaler_encoder_imputer_fit": True, "no_final_split_or_tensor_created": True,
             "limitations": [config["coverage"]["completeness_status"], config["availability"]["limitation"]]}
    run_id = f"{config['config_version']}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"
    output = output_root / run_id
    output.mkdir(parents=True, exist_ok=False)
    for name, path in source_files.items():
        snapshot = output / "source" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(path.read_bytes())
        if sha256_file(snapshot) != source_hashes[name]:
            raise ValueError("Source code changed before snapshot creation")
    save_csv(output / "aggregated/observed_weekly.csv.gz", observed)
    save_csv(output / "panel/panel_weekly.csv.gz", panel)
    save_csv(output / "panel/series_registry.csv.gz", registry)
    save_csv(output / "features/calendar_lags.csv.gz", features)
    for name, value in [("target_eda.json", eda), ("panel_report.json", audit), ("window_design.json", design),
                        ("feature_contract.json", feature_contract), ("config.resolved.json", config), ("current-data.snapshot.json", pointer)]:
        write_json(output / name, value)
    for path, checksum in monitored.items():
        if sha256_file(path) != checksum:
            raise ValueError("A monitored WP3 input changed during processing")
    if any(sha256_file(path) != source_hashes[name] for name, path in source_files.items()):
        raise ValueError("Source code changed during WP3 processing")
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    result = {"manifest_id": run_id, "stage": "WP3_weekly_panel", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "config_version": config["config_version"], "input_manifest_id": manifest["manifest_id"],
              "input_manifest": relative_display(root, manifest_path), "input_manifest_sha256": sha256_file(manifest_path),
              "feature_contract_version": feature_contract["contract_version"],
              "inputs_sha256": {relative_display(root, path): checksum for path, checksum in monitored.items()},
              "source_code_sha256": source_hashes,
              "source_commit": git("rev-parse", "HEAD"), "source_dirty": bool(git("status", "--porcelain")),
              "environment": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
                              "system": platform.system(), "machine": platform.machine(), "cpu_count": os.cpu_count(), "framework": "none"},
              "seed": None, "seed_reason": "Deterministic aggregation and calendar transformations without random operations",
              "counts": eda["counts"], "encoder_weeks": design["encoder_weeks"], "horizon_weeks": design["horizon_weeks"],
              "outputs": {relative_display(output, path): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file()},
              "verification": {"inputs_unchanged": True, "roundtrip_exact": True, "exact_target_conservation": True,
                               "unique_series_week": True, "regular_calendar": True, "past_only_features_under_declared_availability": True},
              "shared_by_all_models": True, "ready_for_split": True, "final_split_created": False,
              "availability_assumption": config["availability"], "coverage_assumption": config["coverage"]["completeness_status"],
              "elapsed_seconds": round(time.perf_counter() - started, 3),
              "privacy": {"row_level_outputs_private": True, "safe_to_commit": False}}
    write_json(output / "data_manifest.json", result)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(f"WP3 panel completed: {run(args.config)}")


if __name__ == "__main__":
    main()
