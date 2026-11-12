# WP7 frozen test evaluation v1

The configuration pins the certified WP6 cohort by SHA256 before the first test
forecast. All nine final checkpoints (three architectures, seeds 42/123/2026)
are evaluated on the same sealed WP4 test windows, in the original sample order.
No model fitting, checkpoint selection, calibration, clipping, rounding, scaler
refit, hyperparameter search or sample filtering is performed in WP7.

The experiment is rolling origin with frozen preprocessing. Previously available
observations may enter later test windows as specified by WP4. Each forecast is
made using only the input dictionary; its horizon labels remain separate.
The source coverage and retrospective availability assumptions of WP3/WP4 apply.
Results describe eligible windows, not cold-start series excluded by WP4.

Point metrics use original pcs: MAE, RMSE, MAPE over actual != 0 with count and
coverage, SMAPE = mean(200 * abs(prediction - actual) /
max(abs(actual) + abs(prediction), 1e-6)), and r2 = 1 - SSE/SST.
MAPE with no nonzero actuals and r2 with constant actuals are null. Negative
predictions are retained and counted. Residual means prediction minus actual.
Per-seed metrics are summarized by arithmetic mean and sample standard deviation
(ddof=1); no metric is calculated from seed-averaged predictions. Seed dispersion
is not a confidence interval or an independent-sample significance test.

Each run stores a byte-identical copy of its final checkpoint, its upstream
manifest, config, source snapshots, environment/hardware, data binding, ordered
predictions, metrics, private error analysis and SHA256 checksums. Training time
in the WP7 manifest is zero; historical WP6 training seconds are separately
linked and carried into the comparison summary.

Inference timing uses CPU float32, two threads and batch 256, one warm-up and
five measured repetitions. Every repetition must produce exactly the same
ordered forecasts. The first timed prediction is the saved forecast; the median
of all timed repetitions is the reported inference time. Timing includes the
existing adapter's batch conversion/forward/collection scope. Loading, feature
construction, inverse scaling, writing and metric/error analysis are outside
that scope. Repetitions measure performance only and cannot change model choice.
Wall-clock execution is not a controlled hardware benchmark; host load and CPU
peak memory are not measured. Existing WP6 retry-resource limitations still apply.

Country, sales-group, month, horizon, series, zero-demand, sparse-series and
target-tail diagnostics stay private. Sparse-series classification uses each
series' fraction of zero weeks in unique train-panel rows available at the train
cutoff, with threshold 0.8. Series absent from train get no_train_history.
The target-tail threshold is the 0.99 train-panel target quantile. These
thresholds are frozen before test; they never filter forecasts or change models.
Grouped metrics require 20 observations and three series; series-only diagnostics
require 20 observations. Smaller groups retain counts and suppression markers.
Residual quantiles, negative forecasts, largest absolute errors, and pooled
lag-one correlation over adjacent weekly residual pairs are descriptive checks.
No significance or normality claim is inferred from overlapping histories or
dependent series. With horizon one, there is only one horizon stratum.

Detailed CSV/JSON outputs and category labels stay under ignored artifacts/.
Any tracked report is manually reviewed and contains only model-level aggregate
results, methods, counts, run references and limitations. WP8 owns the final
accuracy/efficiency/interpretability decision. A low test error alone does not
establish a universally best model.

Run and independently verify locally:

```powershell
.\.venv\Scripts\python.exe -B -m src.evaluation.test_evaluation --config configs/evaluation.wp7.v1.json
.\.venv\Scripts\python.exe -B -m src.evaluation.test_evaluation --verify artifacts/runs/<wp7-evaluation-id>/evaluation.json
```

Verification checks upstream evidence, immutable tensors, model/seed coverage,
checkpoint provenance, identical sample digests, saved predictions and recomputed
metrics/diagnostics/summary. It reruns deterministic inference without fitting or
replacing the original timing observations. Each new evaluation uses unique paths.
