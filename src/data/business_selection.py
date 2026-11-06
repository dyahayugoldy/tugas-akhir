"""WP2: deterministic business filters, private EDA, and a versioned feature contract.

Run with ``python -m src.data.business_selection --config <path>``. No fitting,
statistical imputation, category learning, raw-source edits, or brand inference
takes place. Missing categories may receive explicitly approved reserved tokens.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from src.data.harmonize_workbooks import ROOT, SYSTEM_COLUMNS, load_json, relative_display, relative_path, sha256_file


def number(value: object) -> Decimal | None:
    """Parse canonical decimal text, preserving zero and rejecting nonfinite values."""
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def normalize(value: str) -> str:
    return value.strip().upper()


def apply_missing_categories(frame: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, dict]:
    """Apply approved missing-category tokens without learning from other rows."""
    result = frame.copy()
    report = {}
    for field, policy in config["quality_policy"].get("categorical_missing", {}).items():
        blank = result[field].str.strip().eq("")
        if result.loc[~blank, field].map(normalize).eq(policy["token"]).any():
            raise ValueError("Reserved missing-category token collides with source values")
        result[policy["indicator"]] = blank.map({True: "true", False: "false"})
        result.loc[blank, field] = policy["token"]
        report[field] = {
            "action": policy["action"], "token": policy["token"], "rows": int(blank.sum()),
            "by_source_year": {str(y): int(blank[result["source_year"].eq(str(y))].sum()) for y in config["source_years"]},
        }
    return result, report


def exclusion_reasons(row: dict, config: dict) -> list[str]:
    reasons = []
    for rule in config["filters"]:
        value = row[rule["field"]]
        if rule["operator"] == "in":
            matched = normalize(value) in rule["values"]
        elif rule["operator"] == "numeric_equal":
            parsed = number(value)
            matched = parsed is not None and parsed == Decimal(rule["value"])
        else:
            raise ValueError("Unsupported filter operator")
        if matched:
            reasons.append(rule["id"])
    return reasons


def quality_holds(row: dict, policy: dict) -> list[str]:
    holds = []
    date_value = row[policy["date_field"]]
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        holds.append("primary_date_missing_or_invalid")
    for field in policy["required_codes"]:
        if not row[field].strip():
            holds.append(f"{field}_missing")
    for field in policy["numeric_fields"]:
        value = number(row[field])
        if value is None:
            holds.append(f"{field}_missing_or_invalid")
        elif value < 0:
            holds.append(f"{field}_negative")
    return holds


def validate_config(config: dict, contract: dict, schema: dict) -> None:
    if config["category_normalization"] != "strip_upper":
        raise ValueError("Unsupported categorical normalization")
    supported_policies = {
        "missing_invalid": "retain_with_hold_no_imputation",
        "negative_numeric": "retain_with_hold_pending_business_review",
        "zero_target": "retain_observed_zero",
        "duplicates": "retain_report_only",
        "date_gap": "retain_wp1_flags_no_additional_gap_filter",
    }
    if any(config["quality_policy"][key] != value for key, value in supported_policies.items()):
        raise ValueError("Unsupported quality policy")
    names = [column["canonical_name"] for column in schema["column_definitions"]]
    features = [column["field"] for column in contract["columns"]]
    if len(features) != len(set(features)) or set(features) != set(names):
        raise ValueError("Feature contract must classify every canonical source column exactly once")
    if any(not column.get(key) for column in contract["columns"] for key in ["class", "transform", "availability", "reason"]):
        raise ValueError("Every feature needs a class, transform, availability, and rationale")
    policy = config["quality_policy"]
    required = [policy["date_field"], policy["target_field"]] + policy["numeric_fields"] + policy["required_codes"]
    if not set(required).issubset(names):
        raise ValueError("Quality policy references an undeclared column")
    if not set(config["eda"]["candidate_group_fields"]).issubset(names):
        raise ValueError("EDA grouping references an undeclared column")
    for field, policy in config["quality_policy"].get("categorical_missing", {}).items():
        if field not in names or policy["action"] != "explicit_unknown":
            raise ValueError("Unsupported missing-category policy")
        if not policy["token"] or policy["token"] != normalize(policy["token"]):
            raise ValueError("Missing-category token must be normalized and nonempty")
        if policy["indicator"] in names or policy["indicator"] not in contract["system_columns"]:
            raise ValueError("Missing-category indicator must be declared as audit metadata")
        approved = contract.get("country_missing", {})
        if any(approved.get(key) != value for key, value in {
            "field": field, "action": policy["action"], "token": policy["token"], "indicator": policy["indicator"]
        }.items()):
            raise ValueError("Missing-category contract mismatch")
        if policy["token"] == contract["preprocessing"]["unknown_token"]:
            raise ValueError("Missing country and unseen-category tokens must be distinct")
    ids = [rule["id"] for rule in config["filters"]]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("Filter IDs must be unique and nonempty")
    for rule in config["filters"]:
        if rule["field"] not in names:
            raise ValueError("Filter references an undeclared column")
        if rule["operator"] == "in":
            if not rule["values"] or any(v != normalize(v) or not v for v in rule["values"]):
                raise ValueError("Filter category values must be normalized and nonblank")
        elif rule["operator"] == "numeric_equal":
            if number(rule["value"]) is None:
                raise ValueError("Numeric filter must have a finite value")
        else:
            raise ValueError("Unsupported filter operator")
    if contract["target"]["field"] != config["quality_policy"]["target_field"]:
        raise ValueError("Target contract mismatch")
    if contract["time"]["primary"] != config["quality_policy"]["date_field"]:
        raise ValueError("Time contract mismatch")
    if contract["time"]["week_frequency"] != config["eda"]["week_frequency"]:
        raise ValueError("Weekly calendar contract mismatch")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def target_eda(frame: pd.DataFrame, config: dict) -> dict:
    """Private descriptive diagnostics. Never used to fit or choose a model."""
    policy, settings = config["quality_policy"], config["eda"]
    values = pd.to_numeric(frame[policy["target_field"]], errors="coerce")
    values = values.where(values.map(lambda v: pd.notna(v) and math.isfinite(v)))
    valid = values.dropna()
    result = {
        "rows": len(frame), "missing_or_invalid": int(values.isna().sum()),
        "zero": int(values.eq(0).sum()), "negative": int(values.lt(0).sum()),
        "noninteger": int((valid % 1 != 0).sum()),
        "mean": _finite(valid.mean()), "std": _finite(valid.std(ddof=0)),
        "quantiles": {str(q): _finite(valid.quantile(q)) for q in settings["quantiles"]},
        "weekly_scope": settings["weekly_diagnostics"],
    }
    dated = frame.copy()
    dated["_date"] = pd.to_datetime(dated[policy["date_field"]], format="%Y-%m-%d", errors="coerce")
    dated["_quantity"] = values
    dated = dated.loc[dated["_date"].notna() & values.notna()].copy()
    result["dated_target_rows"] = len(dated)
    if dated.empty:
        result["weekly"] = None
        return result
    dated["_week"] = dated["_date"].dt.to_period(settings["week_frequency"]).dt.start_time
    weekly = dated.groupby("_week")["_quantity"].sum(min_count=1)
    # Missing weeks are kept missing; the zero-fill decision belongs to WP3.
    calendar = pd.date_range(weekly.index.min(), weekly.index.max(), freq="7D")
    diagnostic_groups = []
    for _, group in dated.groupby(settings["candidate_group_fields"], dropna=False):
        weeks = group["_week"].nunique()
        span = int((group["_week"].max() - group["_week"].min()).days // 7 + 1)
        diagnostic_groups.append({"observed_weeks": weeks, "span_weeks": span, "absent_weeks": span - weeks})
    mean, std = float(weekly.mean()), float(weekly.std(ddof=0))
    result["weekly"] = {
        "observed_weeks": len(weekly), "span_weeks": len(calendar),
        "absent_weeks": len(calendar.difference(weekly.index)),
        "observed_zero_total_weeks": int(weekly.eq(0).sum()),
        "coefficient_of_variation": std / abs(mean) if mean != 0 else None,
        "mean_absolute_change_adjacent_observed_calendar_weeks": _finite(weekly.reindex(calendar).diff().abs().mean()),
        "candidate_country_series_count": len(diagnostic_groups),
        "candidate_country_week_counts": diagnostic_groups,
        "no_zero_imputation": True, "not_final_series_definition": True,
    }
    return result


def run(config_path: Path, *, root: Path = ROOT) -> Path:
    started = time.perf_counter()
    root = root.resolve()
    config_path = config_path.resolve()
    config = load_json(config_path)
    resolve = lambda p: relative_path(root, p, description="WP2 path")
    pointer_path = resolve(config["current_data_pointer"])
    pointer = load_json(pointer_path)
    manifest_path = resolve(pointer["active_data_manifest"])
    manifest = load_json(manifest_path)
    schema_path = resolve(config["harmonization_config"])
    schema = load_json(schema_path)
    contract_path = resolve(config["feature_contract"])
    contract = load_json(contract_path)
    validate_config(config, contract, schema)
    if manifest["canonical_schema_version"] != schema["canonical_schema_version"]:
        raise ValueError("Input manifest schema version mismatch")
    input_path = manifest_path.parent / manifest["outputs"]["canonical_transactions"]
    input_path = resolve(relative_display(root, input_path))
    if sha256_file(input_path) != manifest["outputs"]["canonical_transactions_sha256"]:
        raise ValueError("Canonical input checksum mismatch")
    cleaning_path = resolve(manifest["input_cleaning_manifest"])
    if sha256_file(cleaning_path) != manifest["input_cleaning_manifest_sha256"]:
        raise ValueError("Cleaning manifest checksum mismatch")
    cleaning = load_json(cleaning_path)
    source_lookup = {Path(p).name: resolve(p) for p in cleaning["config"]["sources"]}
    if {int(s["source_year"]) for s in manifest["sources"]} != set(config["source_years"]):
        raise ValueError("Input source years differ from WP2 config")
    monitored = {path: sha256_file(path) for path in [config_path, pointer_path, manifest_path, schema_path, contract_path, cleaning_path, input_path]}
    for source in manifest["sources"]:
        source_path = source_lookup[source["source_file"]]
        actual = sha256_file(source_path)
        if actual != source["source_sha256"]:
            raise ValueError("Raw source checksum mismatch")
        monitored[source_path] = actual
    frame = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    expected = [d["canonical_name"] for d in schema["column_definitions"]]
    if list(frame.columns) != SYSTEM_COLUMNS + expected:
        raise ValueError("Canonical input lacks contract columns")
    if len(frame) != manifest["row_count"]:
        raise ValueError("Input row count differs from manifest")
    if set(frame["source_year"].astype(int)) != set(config["source_years"]):
        raise ValueError("Canonical rows do not cover expected years")
    lineage = ["source_file", "source_year", "source_sheet", "source_row_number"]
    if frame.duplicated(lineage).any():
        raise ValueError("Input lineage must uniquely identify each source row")
    rows = frame.to_dict("records")
    reasons = [exclusion_reasons(row, config) for row in rows]
    keep = pd.Series([not value for value in reasons], index=frame.index)
    selected = frame.loc[keep].copy()
    # Normalize only filter-code columns, preserving NA as a literal country code.
    for rule in config["filters"]:
        if rule["operator"] == "in":
            selected[rule["field"]] = selected[rule["field"]].map(normalize)
    selected, categorical_missing_report = apply_missing_categories(selected, config)
    holds = [quality_holds(row, config["quality_policy"]) for row in selected.to_dict("records")]
    selected["wp2_quality_holds"] = ["|".join(value) for value in holds]
    if any(exclusion_reasons(row, config) for row in selected.to_dict("records")):
        raise AssertionError("An excluded category remains after selection")
    rule_ids = [rule["id"] for rule in config["filters"]]

    def summary(indices: list[int]) -> dict:
        counter = Counter(reason for i in indices for reason in reasons[i])
        overlaps = Counter("|".join(reasons[i]) for i in indices if reasons[i])
        removed = sum(bool(reasons[i]) for i in indices)
        return {"before": len(indices), "excluded_unique": removed, "after": len(indices) - removed,
                "rule_matches_including_overlap": {key: counter[key] for key in rule_ids},
                "exclusive_reason_combinations": dict(sorted(overlaps.items())),
                "rows_matching_multiple_rules": sum(len(reasons[i]) > 1 for i in indices)}

    report = {"all": summary(list(frame.index)), "by_source_year": {
        str(year): summary(frame.index[frame["source_year"].eq(str(year))].tolist()) for year in config["source_years"]}}
    report["remaining_rule_matches"] = {key: 0 for key in rule_ids}
    report["categorical_missing_handling"] = categorical_missing_report
    buyer_field = contract["buyer_mapping"]["canonical"]
    report["buyer_missingness"] = {
        "field": buyer_field,
        "before": int(frame[buyer_field].str.strip().eq("").sum()),
        "after": int(selected[buyer_field].str.strip().eq("").sum()),
        "by_source_year": {str(y): {
            "before": int(frame.loc[frame["source_year"].eq(str(y)), buyer_field].str.strip().eq("").sum()),
            "after": int(selected.loc[selected["source_year"].eq(str(y)), buyer_field].str.strip().eq("").sum()),
        } for y in config["source_years"]},
    }
    report["quality_holds"] = {"rows": sum(bool(h) for h in holds), "reasons": dict(Counter(h for hs in holds for h in hs))}
    report["quality_status_after"] = selected["quality_status"].value_counts().to_dict()
    report["canonical_duplicate_rows_after"] = int(selected.duplicated(expected, keep="first").sum())
    report["missingness_before"] = {c: int(frame[c].str.strip().eq("").sum()) for c in expected}
    report["missingness_after"] = {c: int(selected[c].str.strip().eq("").sum()) for c in expected}
    eda = {"scope": config["eda"]["scope"], "target_unit_status": contract["target"]["unit_status"],
           "target_unit": contract["target"]["unit"],
           "before": target_eda(frame, config), "after": target_eda(selected, config),
           "by_source_year_after": {str(y): target_eda(selected[selected["source_year"].eq(str(y))], config) for y in config["source_years"]}}
    blockers = []
    if contract["target"]["unit"] is None or contract["target"]["unit_status"] != "business_confirmed":
        blockers.append("quantity_unit_requires_business_confirmation")
    if contract["series"]["status"] != "business_confirmed":
        blockers.append("brand_or_category_mapping_or_fallback_requires_approval")
    if report["quality_holds"]["rows"]:
        blockers.append("row_quality_holds_require_resolution_before_panel")
    output_root = resolve(config["output_root"])
    if not output_root.is_relative_to(root / "artifacts"):
        raise ValueError("WP2 detailed outputs must stay under private artifacts")
    run_id = f"{config['config_version']}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"
    output = output_root / run_id
    output.mkdir(parents=True, exist_ok=False)
    selected.to_csv(output / "selected_transactions.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    audit = frame[lineage].copy()
    audit["exclusion_reasons"] = ["|".join(r) for r in reasons]
    audit["included"] = keep
    audit.to_csv(output / "selection_audit.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    write_json(output / "selection_report.json", report)
    write_json(output / "target_eda.json", eda)
    write_json(output / "config.resolved.json", config)
    write_json(output / "feature_contract.json", contract)
    write_json(output / "current-data.snapshot.json", pointer)
    write_json(output / "harmonization-config.snapshot.json", schema)
    for path, expected_hash in monitored.items():
        if sha256_file(path) != expected_hash:
            raise ValueError("An input changed during WP2; run is not publishable")
    # Reopen the saved artifact, not just the in-memory selection, for verification.
    saved = pd.read_csv(output / "selected_transactions.csv.gz", dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(selected.reset_index(drop=True), saved)
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    result = {
        "manifest_id": run_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "WP2_business_selection", "config_version": config["config_version"],
        "canonical_schema_version": manifest["canonical_schema_version"],
        "input_manifest_id": manifest["manifest_id"], "input_manifest": relative_display(root, manifest_path),
        "inputs_sha256": {relative_display(root, p): h for p, h in monitored.items()},
        "source_code_sha256": sha256_file(Path(__file__)), "source_commit": commit,
        "environment": {"python": platform.python_version(), "pandas": pd.__version__, "platform": platform.platform()},
        "seed": None, "seed_reason": "Deterministic selection with no random operations",
        "row_count": len(selected), "excluded_count": int((~keep).sum()),
        "verification": {"inputs_unchanged": True, "roundtrip_exact": True, "no_forbidden_rows": True, "no_statistical_fit_or_imputation": True},
        "outputs": {p.name: sha256_file(p) for p in sorted(output.iterdir())},
        "shared_by_all_models": True, "ready_for_panel": not blockers, "blockers": blockers,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "privacy": {"row_level_outputs_private": True, "safe_to_commit": False},
    }
    write_json(output / "data_manifest.json", result)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    output = run(args.config)
    print(f"WP2 selection completed: {output}")


if __name__ == "__main__":
    main()
