"""Restore all source columns using byte-identical copies and prior verified audits.

This fast rollback is valid only when the original source checksums still match
an exact-duplicate audit with zero duplicates. Otherwise use clean_workbooks
with the preserve_all configuration to recompute row cleaning.
"""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pandas as pd

from src.data.clean_workbooks import ROOT, checksum, validate_config


def restore(config_path: Path) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if config["empty_columns"]["condition"] != "preserve_all":
        raise ValueError("Restoration requires a preserve_all column policy")
    evidence_path = ROOT / config["restoration_evidence"]["duplicate_audit_manifest"]
    audit_path = ROOT / config["restoration_evidence"]["missingness_audit"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit["source_manifest_sha256"] != checksum(evidence_path):
        raise ValueError("Missingness audit does not match the duplicate-audit manifest")
    originals = {record["source_file"]: record for record in evidence["sources"]}
    audited = {record["source_file"]: record for record in audit["sources"]}
    for filename in config["sources"]:
        source = (ROOT / filename).resolve()
        record = originals[source.name]
        if checksum(source) != record["source_sha256"]:
            raise ValueError("Source differs from the previously verified duplicate audit")
        if any(sheet["duplicate_rows_removed"] != 0 for sheet in record["sheets"]):
            raise ValueError("Duplicate rows require recomputation with clean_workbooks")
    output_root = (ROOT / config["output_root"]).resolve()
    if not output_root.is_relative_to(ROOT / "artifacts"):
        raise ValueError("Restricted copies must remain under artifacts/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{config['config_version']}_{stamp}_{uuid.uuid4().hex[:8]}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "config.resolved.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    csv_rows = []
    records = []
    for filename in config["sources"]:
        source = (ROOT / filename).resolve()
        prior = originals[source.name]
        missingness = audited[prior["output_file"]]
        if missingness["source_sha256"] != prior["output_sha256"]:
            raise ValueError("Missingness statistics refer to a different intermediate workbook")
        before = checksum(source)
        output = output_dir / f"{source.stem}_columns_preserved.xlsx"
        shutil.copy2(source, output)
        if checksum(output) != before or checksum(source) != before:
            raise AssertionError("The restored copy must be byte-identical to its untouched source")
        workbook = openpyxl.load_workbook(output, read_only=True, data_only=False)
        sheets = []
        try:
            for worksheet, previous in zip(workbook, prior["sheets"], strict=True):
                if worksheet.title != previous["sheet"]:
                    raise AssertionError("Source sheet names differ from the verified audit")
                worksheet.reset_dimensions()
                headers = list(next(worksheet.iter_rows(values_only=True)))
                if len(headers) != previous["columns_before"]:
                    raise AssertionError("The restored header count differs from the source audit")
                empty = previous["empty_columns_removed"]
                rows = previous["rows_before"]
                prior_profile = next(item for item in missingness["sheets"] if item["sheet"] == worksheet.title)
                column_stats = {item["column"]: item for item in prior_profile["columns"]}
                for name in headers:
                    if name in column_stats:
                        item = column_stats[name]
                    elif name in empty:
                        item = {"missing_count": rows, "missing_percent": 100.0}
                    else:
                        raise ValueError("No verified missingness statistics exist for a restored column")
                    csv_rows.append({"source_file": output.name, "sheet": worksheet.title,
                        "column": name, "row_count": rows, "missing_count": item["missing_count"],
                        "missing_percent": item["missing_percent"], "column_removed": False})
                sheets.append({"sheet": worksheet.title, "rows_before": rows, "rows_after": rows,
                    "duplicate_rows_removed": 0, "columns_before": len(headers),
                    "columns_after": len(headers), "empty_columns_removed": [],
                    "entirely_blank_columns_preserved": empty, "remaining_columns": headers})
        finally:
            workbook.close()
        records.append({"source_file": source.name, "source_sha256": before, "source_unchanged": True,
            "output_file": output.name, "output_sha256": checksum(output), "sheets": sheets,
            "verification": {"byte_identical_to_original": True, "all_columns_preserved": True,
                             "exact_duplicate_evidence_reused_for_identical_source": True}})
        print(json.dumps({"file": output.name, "sheets": [
            {"rows": item["rows_after"], "columns": item["columns_after"]} for item in sheets]}), flush=True)
    pd.DataFrame(csv_rows).to_csv(output_dir / "missingness_by_column.csv", index=False)
    manifest = {"run_id": run_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "active", "config": config, "sources": records,
        "duplicate_audit_manifest_sha256": checksum(evidence_path),
        "missingness_audit_sha256": checksum(audit_path), "source_code_sha256": checksum(Path(__file__)),
        "policy_notes": ["User revoked all column deletion based on missingness.",
            "Preserve every original column, including 100-percent-empty columns.",
            "No imputation or row deletion; zero-duplicate evidence reused only for identical source checksums.",
            "Previous column-deletion artifacts retained as historical, superseded results."]}
    (output_dir / "cleaning_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"All columns restored: {output_dir}", flush=True)
    return output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    restore(parser.parse_args().config)
