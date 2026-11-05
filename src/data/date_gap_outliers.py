"""Audit the elapsed time between KP Date and CRD Date without changing source data.

The configured rule is a business rule, not a statistical claim: an observation
is an outlier candidate when the absolute calendar-day gap is less than one
365-day year or greater than two 365-day years.  Results are written only to a
private, reproducible artifact directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def classify_gaps(
    frame: pd.DataFrame,
    *,
    kp_column: str,
    crd_column: str,
    min_days: int,
    max_days: int,
) -> tuple[pd.DataFrame, dict[str, int | float]]:
    """Return row-level safe gap values and a privacy-safe summary."""

    if kp_column not in frame or crd_column not in frame:
        missing = [name for name in (kp_column, crd_column) if name not in frame]
        raise ValueError(f"Missing required date column(s): {missing}")

    kp = pd.to_datetime(frame[kp_column], errors="coerce")
    crd = pd.to_datetime(frame[crd_column], errors="coerce")
    valid = kp.notna() & crd.notna()
    signed_days = (crd[valid] - kp[valid]).dt.total_seconds() / 86_400
    absolute_days = signed_days.abs()
    outlier = (absolute_days < min_days) | (absolute_days > max_days)
    results = pd.DataFrame(
        {
            "source_row_number": np.flatnonzero(valid.to_numpy()) + 2,
            "signed_gap_days_crd_minus_kp": signed_days.to_numpy(),
            "absolute_gap_days": absolute_days.to_numpy(),
            "is_business_rule_outlier": outlier.to_numpy(),
        }
    )
    summary: dict[str, int | float] = {
        "row_count": int(len(frame)),
        "rows_with_incomplete_dates": int((~valid).sum()),
        "rows_with_both_dates": int(valid.sum()),
        "negative_signed_gap_count": int((signed_days < 0).sum()),
        "outlier_count": int(outlier.sum()),
        "outlier_percent_of_valid_date_pairs": (
            float(100 * outlier.mean()) if len(outlier) else 0.0
        ),
        "minimum_absolute_gap_days": float(absolute_days.min()) if len(absolute_days) else None,
        "maximum_absolute_gap_days": float(absolute_days.max()) if len(absolute_days) else None,
        "median_absolute_gap_days": float(absolute_days.median()) if len(absolute_days) else None,
    }
    return results, summary


def make_boxplot_svg(
    gaps_by_source: dict[str, pd.Series],
    output_path: Path,
    min_days: int,
    max_days: int,
) -> None:
    """Render a dependency-free SVG box plot from aggregate distribution values."""

    all_values = pd.concat(list(gaps_by_source.values()), ignore_index=True)
    upper = max(float(all_values.max()), float(max_days))
    lower = min(0.0, float(all_values.min()))
    pad = max(1.0, (upper - lower) * 0.06)
    lower -= pad
    upper += pad
    width, height = 1000, 640
    left, right, top, bottom = 110, 950, 80, 555
    scale_y = lambda value: bottom - (float(value) - lower) / (upper - lower) * (bottom - top)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#202124}.axis{font-size:13px}.small{font-size:12px}.title{font-size:21px;font-weight:bold}</style>',
        '<text x="500" y="34" text-anchor="middle" class="title">Selisih Absolut CRD Date dan KP Date</text>',
        '<text x="500" y="56" text-anchor="middle" class="small">Box plot distribusi per tahun. Pita hijau: rentang diterima 365–730 hari.</text>',
    ]
    band_top, band_bottom = scale_y(max_days), scale_y(min_days)
    parts.append(f'<rect x="{left}" y="{band_top:.1f}" width="{right-left}" height="{band_bottom-band_top:.1f}" fill="#d9ead3" opacity="0.70"/>')
    for tick in np.linspace(math.floor(lower / 365) * 365, math.ceil(upper / 365) * 365, 6):
        y = scale_y(tick)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#d9d9d9"/>')
        parts.append(f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" class="axis">{int(tick):,}</text>')
    for boundary, label in ((min_days, "365"), (max_days, "730")):
        y = scale_y(boundary)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#38761d" stroke-width="1.5"/>')
        parts.append(f'<text x="{right+5}" y="{y+4:.1f}" class="small">{label} hari</text>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#333"/>')
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#333"/>')
    positions = np.linspace(left + 150, right - 150, len(gaps_by_source))
    for x, (label, values) in zip(positions, gaps_by_source.items(), strict=True):
        q1, median, q3 = values.quantile([0.25, 0.5, 0.75]).tolist()
        iqr = q3 - q1
        in_fence = values[(values >= q1 - 1.5 * iqr) & (values <= q3 + 1.5 * iqr)]
        whisker_low, whisker_high = float(in_fence.min()), float(in_fence.max())
        box_top, box_bottom = scale_y(q3), scale_y(q1)
        parts.extend(
            [
                f'<line x1="{x:.1f}" y1="{scale_y(whisker_high):.1f}" x2="{x:.1f}" y2="{scale_y(whisker_low):.1f}" stroke="#1f4e79" stroke-width="2"/>',
                f'<line x1="{x-22:.1f}" y1="{scale_y(whisker_high):.1f}" x2="{x+22:.1f}" y2="{scale_y(whisker_high):.1f}" stroke="#1f4e79" stroke-width="2"/>',
                f'<line x1="{x-22:.1f}" y1="{scale_y(whisker_low):.1f}" x2="{x+22:.1f}" y2="{scale_y(whisker_low):.1f}" stroke="#1f4e79" stroke-width="2"/>',
                f'<rect x="{x-34:.1f}" y="{box_top:.1f}" width="68" height="{box_bottom-box_top:.1f}" fill="#9fc5e8" stroke="#1f4e79" stroke-width="2"/>',
                f'<line x1="{x-34:.1f}" y1="{scale_y(median):.1f}" x2="{x+34:.1f}" y2="{scale_y(median):.1f}" stroke="#073763" stroke-width="3"/>',
                f'<text x="{x:.1f}" y="{bottom+26}" text-anchor="middle" class="axis">{escape(label)}</text>',
            ]
        )
    parts.extend(
        [
            '<text x="28" y="315" transform="rotate(-90 28 315)" text-anchor="middle" class="axis">Selisih absolut (hari)</text>',
            '<text x="500" y="608" text-anchor="middle" class="small">Kotak: Q1–Q3. Garis tengah: median. Whisker: batas Tukey 1,5 × IQR.</text>',
            '</svg>',
        ]
    )
    output_path.write_text("\n".join(parts), encoding="utf-8")


def run(config_path: Path) -> Path:
    config = load_json(config_path)
    if not config.get("enabled"):
        raise ValueError("This date-gap audit configuration is disabled.")
    manifest_path = ROOT / config["active_data_manifest"]
    manifest = load_json(manifest_path)
    run_id = f"date-gap-v1_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    output_dir = ROOT / config["output_root"] / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "config.resolved.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    date_columns = config["date_columns"]
    bounds = config["accepted_gap_days"]
    source_summaries: list[dict] = []
    gaps_by_source: dict[str, pd.Series] = {}
    for source in manifest["sources"]:
        # The active manifest stores output workbook names beside itself.
        source_path = manifest_path.parent / source["output_file"]
        expected_hash = source["output_sha256"]
        actual_hash = sha256_file(source_path)
        if actual_hash != expected_hash:
            raise ValueError(f"Checksum mismatch for {source_path}; aborting audit.")
        # Restrict parsing to the two date fields; the source workbooks have
        # tens of thousands of rows and all other columns are irrelevant here.
        needed_columns = {date_columns["reference"], date_columns["comparison"]}
        frame = pd.read_excel(
            source_path,
            engine="openpyxl",
            usecols=lambda name: str(name) in needed_columns,
        )
        gaps, summary = classify_gaps(
            frame,
            kp_column=date_columns["reference"],
            crd_column=date_columns["comparison"],
            min_days=bounds["minimum_inclusive"],
            max_days=bounds["maximum_inclusive"],
        )
        dataset = Path(source["output_file"]).stem.replace("_columns_preserved", "")
        gaps_by_source[dataset] = gaps["absolute_gap_days"]
        summary |= {
            "dataset": dataset,
            "source_file": source["output_file"],
            "source_sha256": actual_hash,
        }
        source_summaries.append(summary)

    summary_frame = pd.DataFrame(source_summaries)
    summary_frame.to_csv(output_dir / "date_gap_summary.csv", index=False, encoding="utf-8")
    make_boxplot_svg(
        gaps_by_source,
        output_dir / "kp_crd_absolute_gap_boxplot.svg",
        bounds["minimum_inclusive"],
        bounds["maximum_inclusive"],
    )
    report = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "input_manifest": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
        "summaries": source_summaries,
        "chart": "kp_crd_absolute_gap_boxplot.svg",
        "notes": [
            "Outlier candidates follow the configured business rule, not Tukey IQR.",
            "Rows with either missing date are excluded from the date-gap rule and counted separately.",
            "The chart contains distributions only; no SAP transaction values or identifiers are exported.",
        ],
    }
    (output_dir / "date_gap_manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.config))


if __name__ == "__main__":
    main()
