"""Profile SAP Excel workbooks without changing their source data.

The utility produces a private, reproducible EDA artifact.  It distinguishes
measurements from code/identifier fields, lists every observed categorical
value in separate workbook sheets, and flags numeric values outside Tukey's
1.5-IQR fences.  An outlier is only a candidate for investigation, not an
automatic data-quality error.

All generated files are intended for ``artifacts/`` because they can contain
restricted SAP category labels and business values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
import pandas as pd


PROFILE_VERSION = "1.0"
IQR_MULTIPLIER = 1.5

# These are semantic hints only.  A value stored as a number can still be a
# code, where IQR-based outlier detection would be meaningless.
MEASUREMENT_TOKENS = frozenset(
    {
        "quantity",
        "value",
        "price",
        "amount",
        "rate",
        "percent",
        "percentage",
        "discount",
        "weight",
        "volume",
        "length",
        "width",
        "height",
        "cost",
    }
)
CODE_TOKENS = frozenset(
    {
        "document",
        "customer",
        "material",
        "old item",
        "country key",
        "sales office",
        "sales group",
        "plant",
        "division",
        "organization",
        "channel",
        "company code",
        "item number",
        "purchase order",
        "po number",
        "reason for rejection",
        "delivery status",
        "sku",
        "setcode",
        "wh code",
        "sold to",
        "ship to",
        "partner",
        "employee",
        "user",
    }
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 checksum without loading an entire source file."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_name(value: Any) -> str:
    """Create a comparable, whitespace-normalised column name."""

    return re.sub(r"\s+", " ", str(value).strip().lower())


def contains_token(column_name: str, tokens: Iterable[str]) -> bool:
    normalised = normalise_name(column_name)
    return any(token in normalised for token in tokens)


def is_semantic_code(column_name: str) -> bool:
    """Identify numeric fields that are business codes rather than measures."""

    if contains_token(column_name, MEASUREMENT_TOKENS):
        return False
    return contains_token(column_name, CODE_TOKENS)


def is_datetime_column(series: pd.Series, column_name: str) -> bool:
    return bool(pd.api.types.is_datetime64_any_dtype(series)) or "date" in normalise_name(
        column_name
    )


def infer_column_kind(series: pd.Series, column_name: str) -> tuple[str, str]:
    """Return the analysis kind and an auditable reason for one source column."""

    if is_datetime_column(series, column_name):
        return "temporal", "datetime dtype or a date-labelled source column"
    if pd.api.types.is_bool_dtype(series):
        return "categorical", "boolean values are discrete categories"
    if pd.api.types.is_numeric_dtype(series):
        if is_semantic_code(column_name):
            return "categorical", "numeric storage, but column name indicates a business code"
        return "numeric", "numeric source dtype and no code/identifier name hint"
    return "categorical", "non-numeric source dtype"


def safe_excel_text(value: str) -> str:
    """Prevent an observed source label from being evaluated as an Excel formula."""

    if value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def display_value(value: Any) -> str:
    """Render a category label deterministically while retaining its visible value."""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (float, np.floating)):
        if math.isfinite(float(value)) and float(value).is_integer():
            return str(int(value))
        return format(float(value), ".15g")
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    return str(value)


def finite_float(value: Any) -> float | None:
    """Convert an aggregate statistic to JSON-safe float or null."""

    if value is None or pd.isna(value):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def numeric_outlier_summary(series: pd.Series, source_id: str, column_name: str) -> dict[str, Any]:
    """Summarise Tukey-IQR outlier candidates for a numeric measurement column."""

    values = pd.to_numeric(series, errors="coerce").dropna().astype("float64")
    row: dict[str, Any] = {
        "source_id": source_id,
        "column": column_name,
        "n_valid": int(values.size),
        "n_missing_or_non_numeric": int(series.size - values.size),
        "outlier_method": f"tukey_iqr_{IQR_MULTIPLIER:g}",
    }
    if values.empty:
        return row | {
            "q1": None,
            "q3": None,
            "iqr": None,
            "lower_fence": None,
            "upper_fence": None,
            "minimum": None,
            "maximum": None,
            "outlier_count": 0,
            "outlier_pct_valid": None,
            "note": "No valid numeric values.",
        }

    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    lower_fence = q1 - IQR_MULTIPLIER * iqr
    upper_fence = q3 + IQR_MULTIPLIER * iqr
    outlier_mask = (values < lower_fence) | (values > upper_fence)
    zero_iqr_note = ""
    if iqr == 0:
        zero_iqr_note = (
            " IQR is zero, so any value different from the shared quartile can be flagged; "
            "review this field in business context."
        )
    outlier_count = int(outlier_mask.sum())
    return row | {
        "q1": finite_float(q1),
        "q3": finite_float(q3),
        "iqr": finite_float(iqr),
        "lower_fence": finite_float(lower_fence),
        "upper_fence": finite_float(upper_fence),
        "minimum": finite_float(values.min()),
        "maximum": finite_float(values.max()),
        "outlier_count": outlier_count,
        "outlier_pct_valid": finite_float(100 * outlier_count / values.size),
        "note": "Outlier candidates are values strictly outside the fences." + zero_iqr_note,
    }


def unique_values_frame(series: pd.Series, source_id: str, column_name: str) -> pd.DataFrame:
    """Return every non-null categorical value and its observed frequency."""

    non_null = series.dropna()
    rendered = non_null.map(display_value)
    counts = rendered.value_counts(dropna=False)
    values = counts.rename_axis("category_value").reset_index(name="row_count")
    values["source_id"] = source_id
    values["column"] = column_name
    values["pct_non_missing"] = np.where(
        len(non_null) == 0, np.nan, 100 * values["row_count"] / len(non_null)
    )
    values["category_value"] = values["category_value"].map(safe_excel_text)
    return values[["source_id", "column", "category_value", "row_count", "pct_non_missing"]]


def write_frame(writer: pd.ExcelWriter, frame: pd.DataFrame, sheet_name: str) -> None:
    """Write a frame in a consistent format and freeze its header row."""

    frame.to_excel(writer, sheet_name=sheet_name, index=False)
    worksheet = writer.book[sheet_name]
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column_cells in worksheet.iter_cols():
        width = min(
            45,
            max(12, max((len(str(cell.value or "")) for cell in column_cells), default=10) + 2),
        )
        worksheet.column_dimensions[column_cells[0].column_letter].width = width


def profile_workbooks(sources: list[Path], output_dir: Path) -> dict[str, Any]:
    """Create a private profile workbook and return non-sensitive run metadata."""

    output_dir.mkdir(parents=True, exist_ok=False)
    column_rows: list[dict[str, Any]] = []
    outlier_rows: list[dict[str, Any]] = []
    category_sheets: list[tuple[str, pd.DataFrame]] = []
    source_metadata: list[dict[str, Any]] = []

    for source_path in sources:
        workbook = pd.ExcelFile(source_path)
        source_metadata.append(
            {
                "source_file": source_path.name,
                "sha256": sha256_file(source_path),
                "sheets": workbook.sheet_names,
            }
        )
        for sheet_name in workbook.sheet_names:
            frame = pd.read_excel(workbook, sheet_name=sheet_name)
            source_id = f"{source_path.stem}::{sheet_name}"
            for column_name in frame.columns:
                series = frame[column_name]
                analysis_kind, reason = infer_column_kind(series, str(column_name))
                non_missing = int(series.notna().sum())
                unique_non_missing = int(series.nunique(dropna=True))
                column_rows.append(
                    {
                        "source_id": source_id,
                        "column": str(column_name),
                        "source_dtype": str(series.dtype),
                        "analysis_kind": analysis_kind,
                        "classification_reason": reason,
                        "row_count": int(len(series)),
                        "non_missing_count": non_missing,
                        "missing_count": int(series.isna().sum()),
                        "missing_pct": finite_float(100 * series.isna().mean()),
                        "unique_non_missing": unique_non_missing,
                    }
                )
                if analysis_kind == "categorical":
                    category_sheets.append(
                        (source_id + " | " + str(column_name), unique_values_frame(series, source_id, str(column_name)))
                    )
                elif analysis_kind == "numeric":
                    outlier_rows.append(numeric_outlier_summary(series, source_id, str(column_name)))

    column_frame = pd.DataFrame(column_rows)
    outlier_frame = pd.DataFrame(outlier_rows)
    category_index_rows: list[dict[str, Any]] = []
    output_path = output_dir / "eda_profile.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        overview = pd.DataFrame(
            [
                {"field": "profile_version", "value": PROFILE_VERSION},
                {"field": "created_at_utc", "value": datetime.now(timezone.utc).isoformat()},
                {"field": "sources", "value": len(sources)},
                {"field": "outlier_method", "value": "Tukey 1.5 × IQR; strictly outside fences"},
                {
                    "field": "category_values",
                    "value": "All non-null observed values are in cat_### sheets; values starting =, +, -, or @ are escaped for Excel safety.",
                },
                {
                    "field": "confidentiality",
                    "value": "Private derivative of SAP data. Keep under artifacts/; do not commit or share externally.",
                },
            ]
        )
        write_frame(writer, overview, "readme")
        write_frame(writer, column_frame, "column_types")
        write_frame(writer, outlier_frame, "numeric_outliers")
        for index, (title, category_frame) in enumerate(category_sheets, start=1):
            sheet_name = f"cat_{index:03d}"
            write_frame(writer, category_frame, sheet_name)
            category_index_rows.append(
                {
                    "sheet": sheet_name,
                    "source_id": category_frame.at[0, "source_id"] if not category_frame.empty else title.split(" | ")[0],
                    "column": category_frame.at[0, "column"] if not category_frame.empty else title.split(" | ")[-1],
                    "unique_non_missing": int(len(category_frame)),
                    "note": "All non-null observed category values with frequency.",
                }
            )
        write_frame(writer, pd.DataFrame(category_index_rows), "category_index")

    manifest = {
        "profile_version": PROFILE_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": source_metadata,
        "outputs": {"profile_workbook": output_path.name},
        "method": {
            "categorical_definition": "non-numeric fields plus numeric fields whose names indicate codes/identifiers",
            "numeric_definition": "numeric fields not identified as codes/identifiers",
            "outliers": {"method": "Tukey IQR", "multiplier": IQR_MULTIPLIER, "strict_bounds": True},
        },
        "counts": {
            "columns": int(len(column_frame)),
            "numeric_columns": int((column_frame["analysis_kind"] == "numeric").sum()),
            "categorical_columns": int((column_frame["analysis_kind"] == "categorical").sum()),
            "temporal_columns": int((column_frame["analysis_kind"] == "temporal").sum()),
            "outlier_candidates": int(outlier_frame["outlier_count"].sum()) if not outlier_frame.empty else 0,
        },
    }
    manifest_path = output_dir / "profile_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="Excel source workbooks to profile")
    parser.add_argument("--output-dir", type=Path, required=True, help="New private artifact directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source_paths = [path.resolve() for path in args.sources]
    absent = [str(path) for path in source_paths if not path.is_file()]
    if absent:
        raise FileNotFoundError("Source workbook(s) not found: " + ", ".join(absent))
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Output directory must be new to avoid overwriting an artifact: {output_dir}")
    manifest = profile_workbooks(source_paths, output_dir)
    print(
        "Profile complete: "
        f"{manifest['counts']['columns']} columns; "
        f"{manifest['counts']['numeric_columns']} numeric; "
        f"{manifest['counts']['categorical_columns']} categorical; "
        f"{manifest['counts']['temporal_columns']} temporal; "
        f"{manifest['counts']['outlier_candidates']} IQR outlier candidates."
    )
    print(f"Private output: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
