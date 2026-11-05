"""Build an auditable canonical transaction artifact from cleaned SAP workbooks.

The input must be a verified cleaning manifest.  This module never edits its
inputs: it verifies the original and cleaned-workbook checksums before and
after reading, retains every input row in the canonical artifact, and records
row-level quality status rather than silently dropping observations.

Detailed transactions are written only below ``artifacts/``.  The manifest and
reports contain counts, policies, schema metadata, and checksums—not source
values, customer information, or purchase-order values.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import platform
import time
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SYSTEM_COLUMNS = [
    "source_file",
    "source_year",
    "source_sheet",
    "source_row_number",
    "quality_status",
    "quality_flags",
]
VALID_LOGICAL_TYPES = {"string", "date", "numeric"}


class HarmonizationConfigError(ValueError):
    """Raised when the versioned harmonization contract is internally invalid."""


def sha256_file(path: Path) -> str:
    """Return a SHA-256 checksum without loading the complete file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise HarmonizationConfigError(f"Expected a JSON object in {path}")
    return result


def relative_path(root: Path, configured_path: str, *, description: str) -> Path:
    """Resolve a repository-relative path and reject traversal outside the root."""

    path = (root / configured_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise HarmonizationConfigError(f"{description} must remain inside the repository") from error
    return path


def relative_display(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def is_blank(value: object) -> bool:
    """Treat null, empty text, whitespace, and scalar NaN as blank."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def decimal_text(value: Decimal) -> str:
    """Use non-scientific, normalized text so CSV keeps a stable numeric value."""

    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def parse_date(value: object) -> pd.Timestamp | None:
    """Parse date-like cells; numeric Excel serials use the Excel 1900 epoch."""

    if is_blank(value):
        return None
    try:
        if isinstance(value, (datetime, date, pd.Timestamp)):
            parsed = pd.Timestamp(value)
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            parsed = pd.to_datetime(float(value), unit="D", origin="1899-12-30", errors="coerce")
        else:
            parsed = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    if getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.tz_convert(None)
    return pd.Timestamp(parsed).normalize()


def canonicalize_value(value: object, logical_type: str) -> tuple[str | None, object | None, str]:
    """Return CSV value, parsed value, and one of ``blank``, ``valid``, ``invalid``."""

    if logical_type not in VALID_LOGICAL_TYPES:
        raise HarmonizationConfigError(f"Unsupported logical type: {logical_type}")
    if is_blank(value):
        return None, None, "blank"
    if logical_type == "string":
        return str(value), str(value), "valid"
    if logical_type == "date":
        parsed = parse_date(value)
        return (parsed.date().isoformat(), parsed, "valid") if parsed is not None else (None, None, "invalid")
    try:
        parsed_decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, None, "invalid"
    if not parsed_decimal.is_finite():
        return None, None, "invalid"
    return decimal_text(parsed_decimal), parsed_decimal, "valid"


def new_field_stats(logical_type: str) -> dict[str, int]:
    result = {"total": 0, "blank": 0, "invalid": 0, "valid": 0}
    if logical_type == "numeric":
        result |= {"negative": 0, "zero": 0}
    return result


def update_field_stats(stats: dict[str, int], state: str, parsed: object | None, logical_type: str) -> None:
    stats["total"] += 1
    stats[state] += 1
    if logical_type == "numeric" and state == "valid":
        assert isinstance(parsed, Decimal)
        if parsed < 0:
            stats["negative"] += 1
        elif parsed == 0:
            stats["zero"] += 1


def validate_config(config: dict[str, Any]) -> None:
    """Validate only the contract needed by this generic, configuration-led pipeline."""

    required = {
        "config_version",
        "canonical_schema_version",
        "active_cleaning_manifest",
        "output_root",
        "source_years",
        "sheet_policy",
        "schema_assertions",
        "column_definitions",
        "quality_policy",
        "privacy_policy",
    }
    absent = sorted(required - set(config))
    if absent:
        raise HarmonizationConfigError("Missing configuration fields: " + ", ".join(absent))
    if not isinstance(config["source_years"], dict) or not config["source_years"]:
        raise HarmonizationConfigError("source_years must be a non-empty object")
    if any(not isinstance(year, int) for year in config["source_years"].values()):
        raise HarmonizationConfigError("Every source_years value must be an integer year")
    if config["sheet_policy"] != {"mode": "all_sheets"}:
        raise HarmonizationConfigError("Only the explicit all_sheets policy is supported")

    assertions = config["schema_assertions"]
    if not isinstance(assertions.get("expected_column_count"), int) or assertions["expected_column_count"] <= 0:
        raise HarmonizationConfigError("schema_assertions.expected_column_count must be positive")
    if assertions.get("require_identical_headers") is not True or assertions.get("allow_extra_columns") is not False:
        raise HarmonizationConfigError("WP1 requires identical source headers and no undeclared columns")

    definitions = config["column_definitions"]
    if len(definitions) != assertions["expected_column_count"]:
        raise HarmonizationConfigError("column_definitions must match expected_column_count")
    source_names = [item.get("source_name") for item in definitions]
    canonical_names = [item.get("canonical_name") for item in definitions]
    logical_types = [item.get("logical_type") for item in definitions]
    if any(not isinstance(item, str) or not item for item in source_names + canonical_names):
        raise HarmonizationConfigError("Every column definition requires non-empty source_name and canonical_name")
    if len(set(source_names)) != len(source_names) or len(set(canonical_names)) != len(canonical_names):
        raise HarmonizationConfigError("Source and canonical column names must be unique")
    if any(item not in VALID_LOGICAL_TYPES for item in logical_types):
        raise HarmonizationConfigError("Each column definition requires a supported logical_type")

    policy = config["quality_policy"]
    primary = policy.get("primary_date_field")
    secondary = policy.get("secondary_date_field")
    numeric_fields = policy.get("numeric_fields")
    currency = policy.get("currency_field")
    definitions_by_canonical = {item["canonical_name"]: item for item in definitions}
    if primary not in definitions_by_canonical or secondary not in definitions_by_canonical:
        raise HarmonizationConfigError("Quality date fields must be declared canonical columns")
    if definitions_by_canonical[primary]["logical_type"] != "date" or definitions_by_canonical[secondary]["logical_type"] != "date":
        raise HarmonizationConfigError("Quality date fields must have logical_type date")
    if not isinstance(numeric_fields, list) or not numeric_fields:
        raise HarmonizationConfigError("quality_policy.numeric_fields must be a non-empty list")
    if any(field not in definitions_by_canonical or definitions_by_canonical[field]["logical_type"] != "numeric" for field in numeric_fields):
        raise HarmonizationConfigError("Quality numeric fields must be declared numeric columns")
    if currency not in definitions_by_canonical:
        raise HarmonizationConfigError("quality_policy.currency_field must be a declared canonical column")
    if policy.get("primary_date_missing_or_invalid") != "rejected" or policy.get("secondary_date_missing_or_invalid") != "flagged":
        raise HarmonizationConfigError("WP1 date status policy differs from the approved contract")
    if policy.get("numeric_missing_or_invalid") != "flagged" or policy.get("negative_numeric") != "flagged":
        raise HarmonizationConfigError("WP1 numeric status policy differs from the approved contract")
    if policy.get("zero_numeric") != "valid_counted_only" or policy.get("blank_currency") != "flagged":
        raise HarmonizationConfigError("WP1 zero/currency status policy differs from the approved contract")
    if policy.get("row_retention") != "retain_all_rows_in_canonical_artifact":
        raise HarmonizationConfigError("WP1 must retain every row in the canonical artifact")
    date_gap = policy.get("crd_gap")
    if not isinstance(date_gap, dict) or date_gap.get("action") != "flag_only":
        raise HarmonizationConfigError("CRD gap candidates must be flag-only in WP1")
    if date_gap.get("reference_field") != primary or date_gap.get("comparison_field") != secondary:
        raise HarmonizationConfigError("CRD gap fields must match the configured date fields")
    if not isinstance(date_gap.get("max_forward_days"), int) or date_gap["max_forward_days"] <= 0:
        raise HarmonizationConfigError("CRD gap max_forward_days must be a positive integer")


def prepare_inputs(config: dict[str, Any], *, root: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    """Verify the active cleaning manifest and return input records with paths."""

    manifest_path = relative_path(root, config["active_cleaning_manifest"], description="active_cleaning_manifest")
    artifacts_root = (root / "artifacts").resolve()
    if not manifest_path.is_file() or not manifest_path.is_relative_to(artifacts_root):
        raise HarmonizationConfigError("active_cleaning_manifest must be an existing artifact manifest")
    cleaning_manifest = load_json(manifest_path)
    records = cleaning_manifest.get("sources")
    if not isinstance(records, list) or not records:
        raise HarmonizationConfigError("Cleaning manifest has no source records")
    source_years = config["source_years"]
    declared_names = set(source_years)
    observed_names = {item.get("source_file") for item in records}
    if declared_names != observed_names:
        raise HarmonizationConfigError("source_years and cleaning-manifest source files differ")

    source_paths_config = cleaning_manifest.get("config", {}).get("sources", [])
    raw_paths = {Path(str(path)).name: relative_path(root, str(path), description="cleaning source") for path in source_paths_config}
    if set(raw_paths) != observed_names:
        raise HarmonizationConfigError("Cleaning-manifest config sources do not match its source records")

    prepared: list[dict[str, Any]] = []
    for record in records:
        source_name = record["source_file"]
        raw_path = raw_paths[source_name]
        clean_path = (manifest_path.parent / record["output_file"]).resolve()
        if not raw_path.is_file() or not raw_path.is_relative_to((root / "dataset").resolve()):
            raise HarmonizationConfigError(f"Raw source is missing or outside dataset/: {source_name}")
        if not clean_path.is_file() or not clean_path.is_relative_to(artifacts_root):
            raise HarmonizationConfigError(f"Cleaned source is missing or outside artifacts/: {source_name}")
        if sha256_file(raw_path) != record["source_sha256"]:
            raise ValueError(f"Raw source checksum differs from the active cleaning manifest: {source_name}")
        if sha256_file(clean_path) != record["output_sha256"]:
            raise ValueError(f"Cleaned source checksum differs from the active cleaning manifest: {source_name}")
        if record.get("source_unchanged") is not True:
            raise HarmonizationConfigError(f"Cleaning manifest does not prove immutable source: {source_name}")
        sheet_records = record.get("sheets")
        if not isinstance(sheet_records, list) or not sheet_records:
            raise HarmonizationConfigError(f"Cleaning manifest has no sheet evidence: {source_name}")
        if any(not isinstance(sheet.get("rows_after"), int) or sheet["rows_after"] < 0 for sheet in sheet_records):
            raise HarmonizationConfigError(f"Cleaning manifest has invalid post-cleaning row counts: {source_name}")
        prepared.append(
            {
                "record": record,
                "source_name": source_name,
                "source_year": source_years[source_name],
                "raw_path": raw_path,
                "clean_path": clean_path,
                "raw_checksum_before": record["source_sha256"],
                "clean_checksum_before": record["output_sha256"],
                "expected_sheet_rows": {sheet["sheet"]: sheet["rows_after"] for sheet in sheet_records},
            }
        )
    return manifest_path, cleaning_manifest, prepared


def inspect_schema(path: Path, *, expected_headers: list[str], source_name: str, source_year: int) -> list[dict[str, Any]]:
    """Read only workbook headers and fail on schema drift before row processing."""

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    reports: list[dict[str, Any]] = []
    try:
        for worksheet in workbook.worksheets:
            rows = worksheet.iter_rows(values_only=True)
            try:
                header_tuple = next(rows)
            except StopIteration as error:
                raise ValueError(f"Worksheet has no header row: {source_name}/{worksheet.title}") from error
            headers = list(header_tuple)
            if any(not isinstance(name, str) or not name for name in headers):
                raise ValueError(f"Worksheet header must contain non-empty text names: {source_name}/{worksheet.title}")
            if len(headers) != len(set(headers)):
                raise ValueError(f"Worksheet has duplicate header names: {source_name}/{worksheet.title}")
            if headers != expected_headers:
                missing = sorted(set(expected_headers) - set(headers))
                unexpected = sorted(set(headers) - set(expected_headers))
                raise ValueError(
                    f"Schema mismatch in {source_name}/{worksheet.title}; "
                    f"missing={missing}, unexpected={unexpected}, order_matches=False"
                )
            reports.append(
                {
                    "source_file": source_name,
                    "source_year": source_year,
                    "source_sheet": worksheet.title,
                    "column_count": len(headers),
                    "headers": headers,
                    "order_matches_config": True,
                }
            )
    finally:
        workbook.close()
    if not reports:
        raise ValueError(f"Workbook has no worksheets: {source_name}")
    return reports


def assess_row(
    states: dict[str, str], parsed: dict[str, object | None], quality_policy: dict[str, Any]
) -> tuple[str, list[str], int | None]:
    """Apply the documented status policy without removing any source row."""

    flags: list[str] = []
    primary = quality_policy["primary_date_field"]
    secondary = quality_policy["secondary_date_field"]
    if states[primary] != "valid":
        flags.append(f"{primary}_{states[primary]}")
    if states[secondary] != "valid":
        flags.append(f"{secondary}_{states[secondary]}")
    for field in quality_policy["numeric_fields"]:
        state = states[field]
        if state != "valid":
            flags.append(f"{field}_{state}")
        elif isinstance(parsed[field], Decimal) and parsed[field] < 0:
            flags.append(f"{field}_negative")
    currency = quality_policy["currency_field"]
    if states[currency] == "blank":
        flags.append(f"{currency}_blank")
    elif states[currency] == "invalid":
        flags.append(f"{currency}_invalid")

    gap_days: int | None = None
    if states[primary] == "valid" and states[secondary] == "valid":
        reference = parsed[primary]
        comparison = parsed[secondary]
        assert isinstance(reference, pd.Timestamp) and isinstance(comparison, pd.Timestamp)
        gap_days = int((comparison - reference).days)
        date_gap = quality_policy["crd_gap"]
        if gap_days > date_gap["max_forward_days"]:
            flags.append(f"{secondary}_after_{primary}_over_{date_gap['max_forward_days']}_days")

    status = "rejected" if states[primary] != "valid" else ("flagged" if flags else "valid")
    return status, flags, gap_days


def new_source_quality(definitions: list[dict[str, Any]], quality_policy: dict[str, Any]) -> dict[str, Any]:
    fields = {item["canonical_name"]: item["logical_type"] for item in definitions}
    dates = [quality_policy["primary_date_field"], quality_policy["secondary_date_field"]]
    numerics = quality_policy["numeric_fields"]
    return {
        "rows_read": 0,
        "rows_retained": 0,
        "quality_status_counts": Counter(),
        "date_fields": {field: new_field_stats(fields[field]) for field in dates},
        "numeric_fields": {field: new_field_stats(fields[field]) for field in numerics},
        "currency": {"total": 0, "blank": 0, "non_blank": 0, "observed_counts": Counter()},
        "crd_gap": {"evaluable_rows": 0, "forward_over_max_count": 0, "minimum_days": None, "maximum_days": None},
    }


def update_source_quality(
    summary: dict[str, Any],
    states: dict[str, str],
    parsed: dict[str, object | None],
    status: str,
    gap_days: int | None,
    quality_policy: dict[str, Any],
) -> None:
    summary["rows_read"] += 1
    summary["rows_retained"] += 1
    summary["quality_status_counts"][status] += 1
    for field in (quality_policy["primary_date_field"], quality_policy["secondary_date_field"]):
        update_field_stats(summary["date_fields"][field], states[field], parsed[field], "date")
    for field in quality_policy["numeric_fields"]:
        update_field_stats(summary["numeric_fields"][field], states[field], parsed[field], "numeric")
    currency_field = quality_policy["currency_field"]
    currency = summary["currency"]
    currency["total"] += 1
    if states[currency_field] == "blank":
        currency["blank"] += 1
    elif states[currency_field] == "valid":
        currency["non_blank"] += 1
        assert isinstance(parsed[currency_field], str)
        currency["observed_counts"][parsed[currency_field]] += 1

    if gap_days is not None:
        gap = summary["crd_gap"]
        gap["evaluable_rows"] += 1
        gap["minimum_days"] = gap_days if gap["minimum_days"] is None else min(gap["minimum_days"], gap_days)
        gap["maximum_days"] = gap_days if gap["maximum_days"] is None else max(gap["maximum_days"], gap_days)
        if gap_days > quality_policy["crd_gap"]["max_forward_days"]:
            gap["forward_over_max_count"] += 1


def json_safe_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Convert private Counters to ordinary, deterministic JSON structures."""

    return {
        "rows_read": summary["rows_read"],
        "rows_retained": summary["rows_retained"],
        "quality_status_counts": dict(sorted(summary["quality_status_counts"].items())),
        "date_fields": summary["date_fields"],
        "numeric_fields": summary["numeric_fields"],
        "currency": {
            "total": summary["currency"]["total"],
            "blank": summary["currency"]["blank"],
            "non_blank": summary["currency"]["non_blank"],
            "observed_counts": dict(sorted(summary["currency"]["observed_counts"].items())),
        },
        "crd_gap": summary["crd_gap"],
    }


def update_combined_quality(combined: Counter[str], source_summary: dict[str, Any]) -> None:
    combined.update(source_summary["quality_status_counts"])


def run(config_path: Path, *, root: Path = ROOT) -> Path:
    """Create a unique WP1 canonical-data artifact and its private audit evidence."""

    root = root.resolve()
    config_path = config_path.resolve()
    config = load_json(config_path)
    validate_config(config)
    cleaning_manifest_path, cleaning_manifest, inputs = prepare_inputs(config, root=root)
    definitions = config["column_definitions"]
    expected_headers = [item["source_name"] for item in definitions]
    expected_canonical = [item["canonical_name"] for item in definitions]
    types_by_canonical = {item["canonical_name"]: item["logical_type"] for item in definitions}

    schema_sources: list[dict[str, Any]] = []
    for input_record in inputs:
        schema_sources.extend(
            inspect_schema(
                input_record["clean_path"],
                expected_headers=expected_headers,
                source_name=input_record["source_name"],
                source_year=input_record["source_year"],
            )
        )
        if sha256_file(input_record["clean_path"]) != input_record["clean_checksum_before"]:
            raise AssertionError(f"Input cleaned workbook changed during schema inspection: {input_record['source_name']}")
        if sha256_file(input_record["raw_path"]) != input_record["raw_checksum_before"]:
            raise AssertionError(f"Raw workbook changed during schema inspection: {input_record['source_name']}")

    output_root = relative_path(root, config["output_root"], description="output_root")
    artifacts_root = (root / "artifacts").resolve()
    if not output_root.is_relative_to(artifacts_root):
        raise HarmonizationConfigError("output_root must be below artifacts/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{config['config_version']}_{stamp}_{uuid.uuid4().hex[:8]}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "config.resolved.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    started = time.perf_counter()

    canonical_path = output_dir / "canonical_transactions.csv.gz"
    source_reports: list[dict[str, Any]] = []
    total_statuses: Counter[str] = Counter()
    global_digests: dict[str, set[str]] = {}
    duplicate_counts: Counter[str] = Counter()
    canonical_header = SYSTEM_COLUMNS + expected_canonical

    with gzip.open(canonical_path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(canonical_header)
        for input_record in inputs:
            source_summary = new_source_quality(definitions, config["quality_policy"])
            workbook = openpyxl.load_workbook(input_record["clean_path"], read_only=True, data_only=False)
            try:
                for worksheet in workbook.worksheets:
                    row_iter = worksheet.iter_rows(values_only=True)
                    headers = list(next(row_iter))
                    if headers != expected_headers:
                        raise AssertionError(f"Schema changed after inspection: {input_record['source_name']}/{worksheet.title}")
                    for source_row_number, raw_row in enumerate(row_iter, start=2):
                        canonical_values: dict[str, str | None] = {}
                        parsed_values: dict[str, object | None] = {}
                        states: dict[str, str] = {}
                        for definition, raw_value in zip(definitions, raw_row, strict=True):
                            canonical_name = definition["canonical_name"]
                            value, parsed, state = canonicalize_value(raw_value, definition["logical_type"])
                            canonical_values[canonical_name] = value
                            parsed_values[canonical_name] = parsed
                            states[canonical_name] = state
                        status, flags, gap_days = assess_row(states, parsed_values, config["quality_policy"])
                        update_source_quality(
                            source_summary,
                            states,
                            parsed_values,
                            status,
                            gap_days,
                            config["quality_policy"],
                        )
                        digest_payload = [canonical_values[name] for name in expected_canonical]
                        digest = hashlib.sha256(
                            json.dumps(digest_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        ).hexdigest()
                        prior_sources = global_digests.setdefault(digest, set())
                        if prior_sources:
                            if input_record["source_name"] in prior_sources:
                                duplicate_counts["canonical_exact_duplicates_within_source"] += 1
                            else:
                                duplicate_counts["canonical_exact_duplicates_cross_source"] += 1
                        prior_sources.add(input_record["source_name"])
                        writer.writerow(
                            [
                                input_record["source_name"],
                                input_record["source_year"],
                                worksheet.title,
                                source_row_number,
                                status,
                                "|".join(flags),
                            ]
                            + [canonical_values[name] for name in expected_canonical]
                        )
            finally:
                workbook.close()
            update_combined_quality(total_statuses, source_summary)
            if source_summary["rows_read"] != sum(input_record["expected_sheet_rows"].values()):
                raise AssertionError(
                    f"Canonical row count differs from cleaning-manifest rows_after: {input_record['source_name']}"
                )
            if sha256_file(input_record["clean_path"]) != input_record["clean_checksum_before"]:
                raise AssertionError(f"Input cleaned workbook changed during canonicalization: {input_record['source_name']}")
            if sha256_file(input_record["raw_path"]) != input_record["raw_checksum_before"]:
                raise AssertionError(f"Raw workbook changed during canonicalization: {input_record['source_name']}")
            source_reports.append(
                {
                    "source_file": input_record["source_name"],
                    "source_year": input_record["source_year"],
                    "source_sha256": input_record["raw_checksum_before"],
                    "cleaned_input_file": input_record["record"]["output_file"],
                    "cleaned_input_sha256": input_record["clean_checksum_before"],
                    "source_unchanged_after_read": True,
                    "cleaned_input_unchanged_after_read": True,
                    "input_sheet_rows": input_record["expected_sheet_rows"],
                    "quality": json_safe_summary(source_summary),
                }
            )

    canonical_duplicate_count = sum(duplicate_counts.values())

    schema_report = {
        "schema_version": config["canonical_schema_version"],
        "config_version": config["config_version"],
        "input_header_count": len(expected_headers),
        "source_schema": schema_sources,
        "canonical_columns": [
            {
                "source_name": definition["source_name"],
                "canonical_name": definition["canonical_name"],
                "logical_type": definition["logical_type"],
            }
            for definition in definitions
        ],
        "system_columns": SYSTEM_COLUMNS,
        "schema_decision": (
            "All input workbooks must exactly match the declared "
            f"{len(expected_headers)}-column header schema; values are not fabricated for absent columns."
        ),
    }
    quality_report = {
        "config_version": config["config_version"],
        "canonical_schema_version": config["canonical_schema_version"],
        "row_retention_policy": config["quality_policy"]["row_retention"],
        "row_status_policy": {
            "valid": "No configured WP1 quality flag.",
            "flagged": "Retained; at least one non-primary-date quality flag requires review.",
            "rejected": "Retained in the private canonical artifact but excluded from future date-indexed forecasting panels because KP Date is missing or invalid.",
        },
        "date_policy": {
            "kp_date": "Primary operational time candidate; missing or invalid values are rejected from future time-series panel construction.",
            "crd_date": "Business meaning and feature availability are unverified; it is never used as a model feature in WP1.",
            "gap_policy": config["quality_policy"]["crd_gap"],
        },
        "source_reports": source_reports,
        "combined_quality_status_counts": dict(sorted(total_statuses.items())),
        "canonical_exact_duplicate_count": canonical_duplicate_count,
        "canonical_exact_duplicate_counts": dict(sorted(duplicate_counts.items())),
        "canonical_exact_duplicate_counts": dict(sorted(duplicate_counts.items())),
        "duplicate_interpretation": "Count is computed after configured type canonicalization across all rows; rows are retained and no cross-year de-duplication is performed in WP1.",
        "notes": [
            "No imputation, category recoding, currency conversion, outlier removal, or row deletion was performed.",
            "Zero numeric values are retained and counted; negative numeric values are retained and flagged.",
            "Detailed source values, row lineage, and quality flags remain private under artifacts/.",
        ],
    }
    schema_path = output_dir / "schema_report.json"
    quality_path = output_dir / "data_quality_report.json"
    schema_path.write_text(json.dumps(schema_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quality_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "manifest_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_version": config["config_version"],
        "canonical_schema_version": config["canonical_schema_version"],
        "input_cleaning_manifest": relative_display(root, cleaning_manifest_path),
        "input_cleaning_manifest_sha256": sha256_file(cleaning_manifest_path),
        "input_cleaning_run_id": cleaning_manifest.get("run_id"),
        "sources": [
            {
                "source_file": source["source_file"],
                "source_year": source["source_year"],
                "source_sha256": source["source_sha256"],
                "cleaned_input_file": source["cleaned_input_file"],
                "cleaned_input_sha256": source["cleaned_input_sha256"],
                "source_unchanged_after_read": source["source_unchanged_after_read"],
                "cleaned_input_unchanged_after_read": source["cleaned_input_unchanged_after_read"],
            }
            for source in source_reports
        ],
        "outputs": {
            "canonical_transactions": canonical_path.name,
            "canonical_transactions_sha256": sha256_file(canonical_path),
            "schema_report": schema_path.name,
            "schema_report_sha256": sha256_file(schema_path),
            "data_quality_report": quality_path.name,
            "data_quality_report_sha256": sha256_file(quality_path),
        },
        "row_count": sum(source["quality"]["rows_retained"] for source in source_reports),
        "quality_status_counts": dict(sorted(total_statuses.items())),
        "canonical_exact_duplicate_count": canonical_duplicate_count,
        "source_code_sha256": sha256_file(Path(__file__)),
        "environment": {
            "python": platform.python_version(),
            "openpyxl": openpyxl.__version__,
            "pandas": pd.__version__,
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "privacy": {
            "row_level_outputs_private": True,
            "safe_to_commit": False,
        },
    }
    manifest_path = output_dir / "data_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    output = run(args.config)
    print(f"WP1 canonical artifact completed: {output}")


if __name__ == "__main__":
    main()
