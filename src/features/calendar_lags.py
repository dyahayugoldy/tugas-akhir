"""Calendar covariates and auditable past-only target lags on a weekly panel.

Labels remain in the panel artifact, outside the feature table. A lag carries
its source week and availability time. Consumers forecasting several steps
must mask it against the *original* forecast origin, not each future row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


CALENDAR_FIELDS = {
    "calendar_year", "iso_year", "iso_week", "month", "quarter",
    "week_sin", "week_cos", "month_sin", "month_cos",
}


def build_features(panel: pd.DataFrame, target: str, settings: dict) -> pd.DataFrame:
    """Build row-t forecasts with lag sources strictly before week t."""
    required = {"series_id", "week_start", "available_at", target}
    if not required.issubset(panel.columns):
        raise ValueError("Weekly panel lacks feature input columns")
    ordered = panel.sort_values(["series_id", "week_start"]).reset_index(drop=True)
    dates = pd.to_datetime(ordered["week_start"], format="%Y-%m-%d", errors="raise")
    if ordered.duplicated(["series_id", "week_start"]).any():
        raise ValueError("Duplicate series-week before lag construction")
    gaps = dates.groupby(ordered["series_id"]).diff().dropna()
    if not gaps.eq(pd.Timedelta(weeks=1)).all():
        raise ValueError("Lags require a regular weekly calendar within each series")
    iso = dates.dt.isocalendar()
    feature = ordered[["series_id", "week_start"]].copy()
    feature["origin_time"] = ordered["week_start"]
    calendar = {
        "calendar_year": dates.dt.year, "iso_year": iso.year.astype(int),
        "iso_week": iso.week.astype(int), "month": dates.dt.month, "quarter": dates.dt.quarter,
        "week_sin": np.sin(2 * np.pi * (iso.week.astype(float) - 1) / settings["week_cycle_period"]),
        "week_cos": np.cos(2 * np.pi * (iso.week.astype(float) - 1) / settings["week_cycle_period"]),
        "month_sin": np.sin(2 * np.pi * (dates.dt.month - 1) / settings["month_cycle_period"]),
        "month_cos": np.cos(2 * np.pi * (dates.dt.month - 1) / settings["month_cycle_period"]),
    }
    for name in settings["calendar_fields"]:
        feature[name] = calendar[name]
    groups = ordered.groupby("series_id", sort=False)
    for lag in settings["target_lags_weeks"]:
        name = f"target_lag_{lag}"
        source = groups["week_start"].shift(lag)
        available = groups["available_at"].shift(lag)
        valid = source.notna() & pd.to_datetime(available).le(dates)
        feature[name] = groups[target].shift(lag).where(valid, "")
        feature[f"{name}_source_week"] = source.fillna("")
        feature[f"{name}_available_at"] = available.fillna("")
        feature[f"{name}_observed"] = valid.map({True: "true", False: "false"})
        if not pd.to_datetime(source[valid]).lt(dates[valid]).all():
            raise AssertionError("A target lag contains a non-past source")
    return feature


def features_at_origin(features: pd.DataFrame, origin: str, lags: list[int], registry: pd.DataFrame) -> pd.DataFrame:
    """Mask unavailable lags for one fixed (possibly multi-step) origin.

    Future calendar values may be returned; future realized target lags may
    not. Series unknown at the original origin are excluded using registry
    knowledge times, including when future rows have an existing identifier.
    """
    cutoff = pd.Timestamp(origin)
    if registry["series_id"].duplicated().any() or not set(features["series_id"]).issubset(registry["series_id"]):
        raise ValueError("Every feature series requires one registry knowledge time")
    known_at = pd.to_datetime(registry["known_at"], errors="raise")
    known_ids = registry.loc[known_at.le(cutoff), "series_id"]
    result = features.loc[features["series_id"].isin(known_ids)].copy()
    for lag in lags:
        name = f"target_lag_{lag}"
        available = pd.to_datetime(result[f"{name}_available_at"], errors="coerce")
        valid = result[f"{name}_observed"].eq("true") & available.le(cutoff)
        result[name] = result[name].where(valid, "")
        result[f"{name}_observed"] = valid.map({True: "true", False: "false"})
    return result
