"""Zero-aware point forecast metrics shared by every architecture."""

import numpy as np


def point_metrics(actual, prediction, *, smape_epsilon):
    actual, prediction = np.asarray(actual, dtype=np.float64), np.asarray(prediction, dtype=np.float64)
    if actual.shape != prediction.shape or not actual.size:
        raise ValueError("Metrics require matching nonempty prediction and target shapes")
    if not np.isfinite(actual).all() or not np.isfinite(prediction).all() or smape_epsilon <= 0:
        raise ValueError("Metrics require finite values and positive SMAPE epsilon")
    error = prediction - actual
    nonzero = actual != 0
    variance_sum = float(np.square(actual - actual.mean()).sum())
    return {"mae": float(np.abs(error).mean()), "rmse": float(np.sqrt(np.square(error).mean())),
            "mape": float(100 * np.abs(error[nonzero] / actual[nonzero]).mean()) if nonzero.any() else None,
            "mape_count": int(nonzero.sum()), "mape_coverage": float(nonzero.mean()),
            "smape": float((200 * np.abs(error) / np.maximum(np.abs(actual) + np.abs(prediction), smape_epsilon)).mean()),
            "smape_epsilon": smape_epsilon,
            "r2": float(1 - np.square(error).sum() / variance_sum) if variance_sum > 0 else None,
            "r2_defined": variance_sum > 0, "count": int(actual.size),
            "negative_prediction_count": int((prediction < 0).sum()), "unit": "pcs"}
