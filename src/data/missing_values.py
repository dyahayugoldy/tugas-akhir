"""Audit per-column missingness of a previously verified local dataset.

Counts are calculated independently for each workbook/sheet. Empty strings,
nulls and whitespace-only text count as missing. Zero, False and literal
category labels such as NA and N/A remain valid observed values.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import openpyxl
import pandas as pd

from src.data.clean_workbooks import ROOT, checksum, exported_key, export_workbook, is_blank, read_sheet


def profile_frame(frame: pd.DataFrame, threshold_percent: str) -> tuple[list[dict], pd.Series]:
    """Return each column's missing rate and the union of eligible blank rows."""
    threshold = Decimal(threshold_percent)
    if not 0 < threshold <= 100:
        raise ValueError("threshold_percent must be in (0, 100]")
    total = len(frame)
    candidate_rows = pd.Series(False, index=frame.index, dtype=bool)
    columns = []
    for column in frame.columns:
        blank = frame[column].map(is_blank)
        missing_count = int(blank.sum())
        # Compare counts directly, avoiding rounding near the strict cutoff.
        eligible = total > 0 and missing_count > 0 and Decimal(missing_count * 100) < threshold * total
        if eligible:
            candidate_rows |= blank
        columns.append({
            "column": column,
            "row_count": total,
            "missing_count": missing_count,
            "missing_percent": float(Decimal(missing_count * 100) / total) if total else None,
            "eligible_below_threshold": eligible,
        })
    return columns, candidate_rows


def drop_columns(frame: pd.DataFrame, threshold_percent: str) -> tuple[pd.DataFrame, list[dict]]:
    """Project out eligible columns once, retaining every row in its original order."""
    profile, _ = profile_frame(frame, threshold_percent)
    selected = [entry["column"] for entry in profile if entry["eligible_below_threshold"]]
    return frame.drop(columns=selected).copy(), profile


def verify_projection(path: Path, expected: dict[str, pd.DataFrame]) -> dict:
    """Verify all retained cells; projection may legitimately create duplicate rows."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        if workbook.sheetnames != list(expected):
            raise AssertionError("Exported sheets differ from the intended projection")
        for sheet_name, frame in expected.items():
            actual, _ = read_sheet(workbook[sheet_name])
            if actual.shape != frame.shape or list(actual.columns) != list(frame.columns):
                raise AssertionError("Projection changed a row count or the intended column order")
            for intended, observed in zip(frame.itertuples(index=False, name=None), actual.itertuples(index=False, name=None)):
                if any(exported_key(a) != exported_key(b) for a, b in zip(intended, observed)):
                    raise AssertionError("Projection changed a retained value or the row order")
    finally:
        workbook.close()
    return {"row_count_preserved": True, "row_order_preserved": True,
            "all_retained_cells_match_source": True,
            "empty_text_and_null_equivalent_on_excel_readback": True}


def apply_column_deletion(config_path: Path) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("enabled", True) is False:
        raise ValueError("Column deletion was cancelled by the user; use configs/data-cleaning.v2.json")
    if config["action"] != "drop_columns" or config["preserve_all_rows"] is not True:
        raise ValueError("Expected column deletion with all rows preserved")
    if config["comparison"] != "strictly_less_than" or config["exclude_zero_missing"] is not True:
        raise ValueError("Expected the strict positive missingness threshold")
    audit_path = (ROOT / config["source_audit"]).resolve()
    source_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_config = source_audit["config"]
    if Decimal(str(audit_config["threshold_percent"])) != Decimal(str(config["threshold_percent"])):
        raise ValueError("Deletion threshold differs from the audited threshold")
    if (audit_config["comparison"], audit_config["exclude_zero_missing"], audit_config["blank_values"]) != (
        "strictly_less_than", True, ["null", "empty_string", "whitespace_only"]
    ):
        raise ValueError("Audit policies differ from the deletion policy")
    parent_path = (ROOT / audit_config["source_manifest"]).resolve()
    if checksum(parent_path) != source_audit["source_manifest_sha256"]:
        raise ValueError("The parent data manifest differs from the audited manifest")
    output_root = (ROOT / config["output_root"]).resolve()
    if not output_root.is_relative_to(ROOT / "artifacts"):
        raise ValueError("Restricted artifacts must remain under artifacts/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{config['config_version']}_{stamp}_{uuid.uuid4().hex[:8]}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "config.resolved.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    started = time.perf_counter()
    sources = []
    csv_rows = []
    for record in source_audit["sources"]:
        source = parent_path.parent / record["source_file"]
        before = checksum(source)
        if before != record["source_sha256"]:
            raise ValueError("A source workbook differs from the audited version")
        print(f"Filtering columns in {source.name}", flush=True)
        workbook = openpyxl.load_workbook(source, read_only=True, data_only=False)
        expected = {}
        formats = {}
        summaries = []
        audited_sheets = {item["sheet"]: item for item in record["sheets"]}
        try:
            for worksheet in workbook:
                frame, number_formats = read_sheet(worksheet)
                projected, profile = drop_columns(frame, str(config["threshold_percent"]))
                selected = [item["column"] for item in profile if item["eligible_below_threshold"]]
                audited = audited_sheets[worksheet.title]
                if profile != audited["columns"] or len(frame) != audited["rows_before"]:
                    raise AssertionError("Current missingness differs from the audit")
                if not len(projected.columns):
                    raise ValueError("The policy would remove all columns")
                expected[worksheet.title] = projected
                formats[worksheet.title] = number_formats
                retained = [item for item in profile if not item["eligible_below_threshold"]]
                summaries.append({"sheet": worksheet.title,
                    "rows_before": len(frame), "rows_after": len(projected), "rows_removed": 0,
                    "columns_before": len(frame.columns), "columns_after": len(projected.columns),
                    "removed_columns": [item for item in profile if item["eligible_below_threshold"]],
                    "remaining_columns": list(projected.columns),
                    "missingness_before": profile, "missingness_after": retained})
                csv_rows.extend({"source_file": source.name, "sheet": worksheet.title,
                    **item, "column_removed": item["column"] in selected} for item in profile)
                print(json.dumps({"source": source.name, "rows_preserved": len(projected),
                    "columns_before": len(frame.columns), "columns_after": len(projected.columns),
                    "removed_columns": selected}), flush=True)
        finally:
            workbook.close()
        output = output_dir / f"{source.stem}_missing_filtered.xlsx"
        export_workbook(output, expected, formats)
        verification = verify_projection(output, expected)
        if checksum(source) != before:
            raise AssertionError("Source checksum changed during column deletion")
        sources.append({"source_file": source.name, "source_sha256": before,
            "source_unchanged": True, "output_file": output.name, "output_sha256": checksum(output),
            "sheets": summaries, "verification": verification})
        print(f"Verified all retained cells in {output.name}", flush=True)
    pd.DataFrame(csv_rows).to_csv(output_dir / "missingness_by_column.csv", index=False)
    manifest = {"run_id": run_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config, "source_audit_sha256": checksum(audit_path),
        "source_manifest_sha256": checksum(parent_path),
        "source_code_sha256": checksum(Path(__file__)),
        "export_utilities_sha256": checksum(Path(__file__).with_name("clean_workbooks.py")),
        "environment": {"python": platform.python_version(), "pandas": pd.__version__, "openpyxl": openpyxl.__version__},
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "deletion_performed": True, "rows_deleted": 0, "sources": sources,
        "policy_notes": [
            "Drop entire columns with 0 < missing_percent < the configured threshold, separately per source sheet.",
            "Compute the decision once on the input workbook; keep every row in its original order.",
            "Never drop complete columns (0 percent missing) or columns at or above the threshold.",
            "No imputation, category conversion, outlier removal or additional row deduplication.",
            "This is a descriptive data-cleaning artifact; no model feature contract or chronological split is fitted.",
        ]}
    (output_dir / "cleaning_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Column deletion complete and verified: {output_dir}", flush=True)
    return output_dir


def audit(config_path: Path) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["action"] != "profile_only":
        raise ValueError("This audit does not execute deletion actions")
    if config["comparison"] != "strictly_less_than" or config["exclude_zero_missing"] is not True:
        raise ValueError("Expected a strict positive missingness threshold")
    if config["blank_values"] != ["null", "empty_string", "whitespace_only"]:
        raise ValueError("Unsupported blank-cell definition")
    parent_path = (ROOT / config["source_manifest"]).resolve()
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    output_root = (ROOT / config["output_root"]).resolve()
    if not output_root.is_relative_to(ROOT / "artifacts"):
        raise ValueError("Restricted artifacts must remain under artifacts/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{config['config_version']}_{stamp}_{uuid.uuid4().hex[:8]}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "config.resolved.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    sources = []
    csv_rows = []
    for record in parent["sources"]:
        source = parent_path.parent / record["output_file"]
        before = checksum(source)
        if before != record["output_sha256"]:
            raise ValueError("A source workbook no longer matches the parent manifest")
        print(f"Auditing {source.name}", flush=True)
        workbook = openpyxl.load_workbook(source, read_only=True, data_only=False)
        summaries = []
        try:
            for worksheet in workbook:
                frame, _ = read_sheet(worksheet)
                columns, candidates = profile_frame(frame, str(config["threshold_percent"]))
                selected = [item["column"] for item in columns if item["eligible_below_threshold"]]
                summary = {"sheet": worksheet.title, "rows_before": len(frame),
                    "columns": columns, "eligible_columns": selected,
                    "candidate_rows_to_delete": int(candidates.sum()),
                    "candidate_source_excel_rows": (frame.index[candidates] + 2).tolist()}
                summaries.append(summary)
                csv_rows.extend({"source_file": source.name, "sheet": worksheet.title, **item} for item in columns)
                print(json.dumps({"source": source.name, "rows": len(frame),
                    "eligible_columns": [item for item in columns if item["eligible_below_threshold"]],
                    "candidate_rows_to_delete": int(candidates.sum())}), flush=True)
        finally:
            workbook.close()
        if checksum(source) != before:
            raise AssertionError("Source checksum changed during the audit")
        sources.append({"source_file": source.name, "source_sha256": before,
                        "source_unchanged": True, "sheets": summaries})
    pd.DataFrame(csv_rows).to_csv(output_dir / "missingness_by_column.csv", index=False)
    report = {"run_id": run_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config, "source_manifest_sha256": checksum(parent_path),
        "source_code_sha256": checksum(Path(__file__)),
        "environment": {"python": platform.python_version(), "pandas": pd.__version__, "openpyxl": openpyxl.__version__},
        "deletion_performed": False, "sources": sources}
    (output_dir / "missingness_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Read-only audit complete: {output_dir}", flush=True)
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config["action"] == "profile_only":
        audit(args.config)
    elif config["action"] == "drop_columns":
        apply_column_deletion(args.config)
    else:
        parser.error("Unsupported action")


if __name__ == "__main__":
    main()
