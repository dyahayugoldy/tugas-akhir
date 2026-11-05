from __future__ import annotations

import pandas as pd

from src.data.date_gap_outliers import classify_gaps


def test_date_gap_business_rule_boundaries_and_missing_dates() -> None:
    frame = pd.DataFrame(
        {
            "KP Date": pd.to_datetime(["2023-01-01"] * 5),
            "CRD Date": pd.to_datetime(
                ["2023-12-31", "2024-01-01", "2024-12-31", "2025-01-02", None]
            ),
        }
    )
    gaps, summary = classify_gaps(
        frame, kp_column="KP Date", crd_column="CRD Date", min_days=365, max_days=730
    )

    assert gaps["absolute_gap_days"].tolist() == [364.0, 365.0, 730.0, 732.0]
    assert gaps["is_business_rule_outlier"].tolist() == [True, False, False, True]
    assert summary["rows_with_incomplete_dates"] == 1
    assert summary["outlier_count"] == 2
