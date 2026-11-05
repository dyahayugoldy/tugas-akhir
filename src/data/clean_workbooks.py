"""Remove exact duplicate rows and entirely blank columns in private copies.

Run from the project root with --config configs/data-cleaning.v1.json.
No text/category/number normalisation, imputation, or outlier removal is done.
Sources remain immutable; source and output checksums and row lineage are saved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or bool(pd.isna(value))


def exact_key(value: object) -> tuple:
    """Keep text, numeric, boolean and datetime values distinct; nulls compare equal."""
    if value is None or (not isinstance(value, str) and bool(pd.isna(value))):
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    return (type(value).__name__, value)


def exported_key(value: object) -> tuple:
    """Excel readers expose an empty text cell as either empty string or null."""
    return ("null",) if isinstance(value, str) and value == "" else exact_key(value)


def clean_frame(frame: pd.DataFrame, *, drop_empty_columns: bool = True) -> tuple[pd.DataFrame, dict, list[dict]]:
    """Compare original row values, retaining the first occurrence in source order."""
    first_seen: dict[tuple, int] = {}
    kept: list[int] = []
    duplicate_rows: list[dict] = []
    for position, row in enumerate(frame.itertuples(index=False, name=None)):
        key = tuple(exact_key(value) for value in row)
        if key in first_seen:
            duplicate_rows.append({
                "removed_excel_row": position + 2,
                "retained_excel_row": first_seen[key] + 2,
            })
        else:
            first_seen[key] = position
            kept.append(position)

    entirely_blank = [column for column in frame.columns if frame[column].map(is_blank).all()]
    empty_columns = entirely_blank if drop_empty_columns else []
    cleaned = frame.iloc[kept].drop(columns=empty_columns).reset_index(drop=True)
    stats = {
        "rows_before": int(len(frame)),
        "duplicate_rows_removed": len(duplicate_rows),
        "rows_after": int(len(cleaned)),
        "columns_before": len(frame.columns),
        "empty_columns_removed": empty_columns,
        "entirely_blank_columns_detected": entirely_blank,
        "columns_after": len(cleaned.columns),
        "remaining_columns": list(cleaned.columns),
        "retained_source_excel_rows": [position + 2 for position in kept],
    }
    return cleaned, stats, duplicate_rows


def export_workbook(path: Path, sheets: dict[str, pd.DataFrame], formats: dict) -> None:
    """Stream typed cells to a new workbook without loading another full cell grid."""
    workbook = openpyxl.Workbook(write_only=True)
    for sheet_name, frame in sheets.items():
        worksheet = workbook.create_sheet(sheet_name)
        worksheet.freeze_panes = "A2"
        for index, column in enumerate(frame.columns, start=1):
            worksheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = min(38, max(16, len(column) + 2))
        headers = []
        for column in frame.columns:
            cell = openpyxl.cell.WriteOnlyCell(worksheet, column)
            cell.font = openpyxl.styles.Font(name="Arial", size=10, bold=True)
            headers.append(cell)
        worksheet.append(headers)
        for row in frame.itertuples(index=False, name=None):
            cells = []
            for column, value in zip(frame.columns, row):
                if value is None or (not isinstance(value, str) and bool(pd.isna(value))):
                    cells.append(None)
                    continue
                number_format = formats[sheet_name].get(column, "General")
                if number_format != "General" or (isinstance(value, str) and value.startswith("=")):
                    cell = openpyxl.cell.WriteOnlyCell(worksheet, value)
                    cell.number_format = number_format
                    if isinstance(value, str):
                        cell.data_type = "s"
                    cells.append(cell)
                else:
                    cells.append(value)
            worksheet.append(cells)
        end_column = openpyxl.utils.get_column_letter(len(frame.columns))
        worksheet.auto_filter.ref = f"A1:{end_column}{len(frame) + 1}"
    temporary = path.with_suffix(".partial.xlsx")
    workbook.save(temporary)
    temporary.rename(path)


def validate_config(config: dict) -> None:
    if config.get("enabled", True) is False:
        raise ValueError("This configuration was cancelled; use configs/data-cleaning.v2.json")
    if config["duplicates"] != {
        "scope": "within_each_sheet", "compare": "all_original_columns", "keep": "first"
    }:
        raise ValueError("Unsupported duplicate policy")
    policy = config["empty_columns"]
    if policy.get("condition") not in {"all_cells_blank", "preserve_all"} or policy != {
        "condition": policy.get("condition"),
        "blank_values": ["null", "empty_string", "whitespace_only"],
        "preserve_literal_na_strings": True,
    }:
        raise ValueError("Unsupported empty-column policy")
    if not config["sources"] or not config["config_version"].replace("-", "").isalnum():
        raise ValueError("Expected source files and an alphanumeric configuration version")


def read_sheet(worksheet) -> tuple[pd.DataFrame, dict]:
    worksheet.reset_dimensions()
    rows = worksheet.iter_rows()
    headers = [cell.value for cell in next(rows)]
    if not headers or any(not isinstance(label, str) or not label for label in headers):
        raise ValueError("Each input column must have a non-empty text header")
    if len(headers) != len(set(headers)):
        raise ValueError("Duplicate column headers require an explicit schema decision")
    formats = {}
    values = []
    for row in rows:
        if any(cell.data_type == "f" for cell in row):
            raise ValueError("Formula cells require a separate preservation policy")
        values.append([cell.value for cell in row])
        for column, cell in zip(headers, row):
            if column not in formats and cell.value is not None:
                formats[column] = cell.number_format
    frame = pd.DataFrame(values, columns=headers, dtype=object)
    return frame, formats


def verify_export(path: Path, expected: dict[str, pd.DataFrame], *, allow_empty_columns: bool = False) -> dict:
    """Reopen every exported cell and compare it to the intended retained data."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    remaining_empty_columns = 0
    try:
        if workbook.sheetnames != list(expected):
            raise AssertionError("Exported sheet order/name mismatch")
        for sheet_name, frame in expected.items():
            sheet = workbook[sheet_name]
            actual, _ = read_sheet(sheet)
            if actual.shape != frame.shape or list(actual.columns) != list(frame.columns):
                raise AssertionError("Exported shape or header mismatch")
            # Excel dates/numbers may lose sub-microsecond precision on serialization.
            # These SAP exports use ordinary Excel dates and exact stored numbers.
            for want, got in zip(frame.itertuples(index=False, name=None), actual.itertuples(index=False, name=None)):
                if any(exported_key(a) != exported_key(b) for a, b in zip(want, got)):
                    raise AssertionError("Export changed a retained cell value or type")
            second_pass, stats, _ = clean_frame(actual, drop_empty_columns=not allow_empty_columns)
            remaining_empty_columns += len(stats["entirely_blank_columns_detected"])
            if second_pass.shape != actual.shape:
                raise AssertionError("Duplicate rows or disallowed entirely blank columns remain")
    finally:
        workbook.close()
    return {"all_cells_match_retained_source": True,
            "empty_text_and_null_equivalent_on_excel_readback": True,
            "remaining_duplicate_rows": 0,
            "remaining_entirely_blank_columns": remaining_empty_columns,
            "entirely_blank_columns_allowed": allow_empty_columns}


def run(config_path: Path, resume_dir: Path | None = None) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    preserve_columns = config["empty_columns"]["condition"] == "preserve_all"
    sources = [(ROOT / name).resolve() for name in config["sources"]]
    if any(not path.is_file() for path in sources):
        raise FileNotFoundError("A configured source workbook is missing")
    output_root = (ROOT / config["output_root"]).resolve()
    if not output_root.is_relative_to(ROOT / "artifacts"):
        raise ValueError("Restricted outputs must be under the ignored artifacts directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{config['config_version']}_{stamp}_{uuid.uuid4().hex[:8]}"
    if resume_dir is None:
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "config.resolved.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    else:
        run_dir = resume_dir.resolve()
        if run_dir.parent != output_root or (run_dir / "cleaning_manifest.json").exists():
            raise ValueError("Can resume only an incomplete run directly under the configured output root")
        saved_config = json.loads((run_dir / "config.resolved.json").read_text(encoding="utf-8"))
        if saved_config != config:
            raise ValueError("Resume configuration differs from the saved configuration")
        run_id = run_dir.name
    started = time.perf_counter()
    records = []
    for source in sources:
        before = checksum(source)
        print(f"Reading {source.name}", flush=True)
        source_workbook = openpyxl.load_workbook(source, read_only=True, data_only=False)
        expected = {}
        sheet_formats = {}
        sheet_records = []
        lineage = {}
        try:
            for sheet in source_workbook:
                frame, formats = read_sheet(sheet)
                cleaned, stats, duplicates = clean_frame(frame, drop_empty_columns=not preserve_columns)
                if not len(cleaned.columns):
                    raise ValueError("A sheet has no populated columns")
                expected[sheet.title] = cleaned
                sheet_formats[sheet.title] = formats
                lineage[sheet.title] = {"duplicates": duplicates,
                    "retained_source_excel_rows": stats.pop("retained_source_excel_rows")}
                sheet_records.append({"sheet": sheet.title, **stats})
                print(f"  {stats['rows_before']} -> {stats['rows_after']} rows; "
                      f"{stats['columns_before']} -> {stats['columns_after']} columns", flush=True)
        finally:
            source_workbook.close()
        output = run_dir / f"{source.stem}_cleaned.xlsx"
        if not output.exists():
            export_workbook(output, expected, sheet_formats)
        verification = verify_export(output, expected, allow_empty_columns=preserve_columns)
        if checksum(source) != before:
            raise AssertionError("Source checksum changed during processing")
        (run_dir / f"{source.stem}_row_lineage.json").write_text(json.dumps(lineage), encoding="utf-8")
        records.append({"source_file": source.name, "source_sha256": before,
                        "source_unchanged": True, "output_file": output.name,
                        "output_sha256": checksum(output), "sheets": sheet_records,
                        "verification": verification})
        print(f"Verified all retained cells in {output.name}", flush=True)
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "source_code_sha256": checksum(Path(__file__)),
        "environment": {"python": platform.python_version(), "pandas": pd.__version__,
                        "openpyxl": openpyxl.__version__},
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "sources": records,
        "policy_notes": [
            "Exact duplicates compared across all original columns within each source sheet; first source row retained.",
            ("All columns preserved regardless of missingness, including entirely blank columns."
             if preserve_columns else "Only entirely blank columns removed; partial missingness retained."),
            "No category trimming, case folding, imputation, currency conversion or outlier removal.",
            "No learned transformation, split selection, or cross-file deduplication.",
            "Excel readback compares null and empty text as blank; all non-empty values compared exactly.",
            "Source row numbers and duplicate-to-first-occurrence lineage stored privately.",
        ],
    }
    (run_dir / "cleaning_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Completed and verified: {run_dir}", flush=True)
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume-dir", type=Path, help="Resume an incomplete artifact with an identical saved configuration")
    args = parser.parse_args()
    run(args.config, args.resume_dir)


if __name__ == "__main__":
    main()
