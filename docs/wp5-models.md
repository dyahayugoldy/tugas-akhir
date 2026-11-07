# WP5 model specification and adaptations v1

This stage proves model implementation and data compatibility. The one-epoch
acceptance runs are not official WP6 experiments or evidence of model superiority.
All data, predictions, weights, detailed metrics, trial histories, category
vocabularies and source snapshots remain under ignored `artifacts/`.

## References

- Wei et al. (2025), *Multiple objectives escaping bird search optimization and
  its application in stock market prediction based on transformer model*,
  Scientific Reports 15, 5730, <https://doi.org/10.1038/s41598-025-88883-8>.
- L'Heureux, Grolinger and Capretz (2022), *Transformer-Based Model for Electrical
  Load Forecasting*, Energies 15(14), 4993,
  <https://doi.org/10.3390/en15144993>. This is the N-Space reference in sempro;
  the later 2026 memory-augmented AQI variant is not used.
- Lim, Arik, Loeff and Pfister (2020, arXiv v3; journal 2021), *Temporal Fusion
  Transformers for Interpretable Multi-horizon Time Series Forecasting*,
  <https://arxiv.org/abs/1912.09363v3>. This supplies the original architecture;
  Hu (2021) in sempro is an application reference, not the origin of TFT.

References were checked on 7 November 2026. Implementations are project-native
PyTorch adaptations, not claims of exact replication of the original experiments.

## Shared data and interface

`ModelData` inherits the WP4 NumPy loader without modifying split, preprocessing,
sample eligibility or order. It binds an immutable manifest ID and SHA256,
field order, horizon, encoder length, train vocabulary cardinalities and target
scaler. `ForecastAdapter` implements `fit`, `predict`, `save`, `load`, `metadata`
and an input-only `forward`. `fit` returns the model plus a training record;
the acceptance runner combines it with tuning and provenance into `run_manifest`.

The active WP4 inputs comprise a 12-week encoder and a one-week horizon. Numeric
encoder channels are historical quantity plus four lags, with nine calendar
channels. The decoder receives the same calendar fields and origin-available
lags; country and brand are static categories. Encoder/decoder shapes and field
orders come from the sealed tensor schema, never source constants. Models are
global across series; series hashes remain metadata, not embedding inputs.

Unavailable values are replaced using `where(mask, value, 0)` and an explicit
availability channel. Zero in scaled space is not interpreted as zero demand.
Unknown category ID 0 has a valid embedding without `padding_idx`; it is distinct
from the business category for a missing country. The unseen embedding is seeded
but has no training support unless unknown IDs occur in train; this is a cold
category limitation, not a learned estimate of the new category's behaviour.

Tensor conversion is float32, categories int64 and masks bool. All networks return
`[batch, horizon]` scaled point forecasts. Predictions are inverse-scaled to pcs
in float64, without rounding, clipping or forcing negative predictions to zero.
The raw decimal audit artefacts remain the source for exact quantity reconciliation.

Historical target revisions and annual source completeness retain the WP3/WP4
retrospective availability and coverage assumptions. The actual input contract
excludes unverified price, Season, currency and Material features proposed in
sempro. Calendar encodings do not validate the SAP Season column. Quantity is the
target, so no exchange-rate conversion is needed. These deviations must carry
forward into Bab III and the final interpretation of multivariate forecasting.

## Architectures

### MoEBS-Transformer

A standard continuous encoder-decoder Transformer uses separate linear input
projections, static embeddings, sinusoidal positions, full historical encoder
attention and a causally masked decoder. Queries contain calendar and available
lags only. The final linear layer forecasts quantity. MoEBS optimizes the core's
hyperparameters externally; it is not a mixture-of-experts architecture.

The search implements normalized-position EBS equations 6-15: paired predator and
prey, velocity-dependent maneuver power, body factors, escaping/capturing rates,
opposite position for vertical escape, random horizontal escape, and predator
motion toward prey and an archive leader. Beta is sampled from {0, 2}. The
multi-objective extension retains a non-dominated archive, recomputes grid cells,
prunes a most crowded cell and samples leaders with inverse cell occupancy.

Explicit adaptations to underspecified multi-objective/discrete details:

- Pair body cost is the count of dominating population members. Equal ranks use
  equal body factors; zero total maneuver power uses ER=0.5. Incomparable pair
  roles and incomparable greedy replacements use seeded fair coin flips.
- Parameters use [0,1] coordinates, clipped to bounds. Head count and model width
  are decoded to valid categorical choices; learning rate and weight decay use
  log coordinates. The same seed initializes every candidate to reduce noise.
- Objectives minimize validation RMSE, validation MAE and trainable parameter
  count. Deterministic parameter count is a compute proxy, not measured latency.
  Model width is added to the original heads/lr/regularization search so that
  complexity can vary; candidates remain deliberately small.
- The paper's test errors in optimization and Pareto selection are replaced by
  validation errors. Test is inaccessible to the evaluator. Final selection is
  lexicographic RMSE, MAE, parameter count, trial ID across the complete evaluated
  Pareto set, even when a grid-capacity archive has pruned members.
- Candidate fitting uses AdamW MSE and one full epoch in WP5. This is a small
  acceptance budget, not paper-scale optimization or a convergence claim.

All attempts count toward NFE, including timeout/numerical failures. Every
completed trial preserves its configuration, objectives, checkpoint, validation
predictions, epoch/sample counts, seed and timing. Caps apply to evaluations,
iterations and wall-clock time. Timing limits are cooperative at batch/epoch
boundaries; an over-budget completed fit is rejected. A failure produces an audit
file and prevents a successful stage claim when no candidate can be selected.

### N-Space Transformer

The contextual module keeps quantity and context paths separate until fusion.
Country/brand embeddings and calendar encodings accompany historical quantities
and lags. The shared bias-free learnable projection implements X @ A (paper eq. 5),
followed by sinusoidal positions, two encoder and two decoder layers, and a scalar
output projection. Attention head count and width are configurable.

The paper's training description permits current target values in decoder input
without a right shift, which does not satisfy this project's leakage contract.
This implementation instead uses the last available encoder target for the first
decoder token and its own prior forecast for later steps, identically in training
and inference. No future label is fed to the model, including during training.
At each step only that step's origin-available lags/calendar and static context
are attached. A causal mask additionally protects decoder self-attention. There
is no 2026 Attention Memory module. Shared train-only WP4 standard scaling replaces
any original-domain preprocessing; there is no per-model scaler fit.

### Temporal Fusion Transformer

TFT includes static embeddings and static variable selection; four separate GRNs
provide selection, enrichment and initial LSTM hidden/cell contexts. Separate
encoder and decoder variable-selection networks process observed/known channels,
followed by recurrent encoder/decoder, gated residual locality, static enrichment,
causal interpretable temporal attention, position-wise GRN and final gated skip.
Interpretable attention uses separate Q/K for each head, shared V and the mean of
head outputs (paper eqs. 13-16), rather than ordinary concatenated multihead output.

Numeric availability is paired with its variable rather than interpreted as an
extra business feature. Decoder lag channels are known only where the WP4 origin
mask permits them; unrealized decoder target is absent. This extends the basic
known-future decoder grouping to origin-available lag regressors consistently
across all three models.

Original TFT quantile outputs/loss are replaced with a single point output and
shared MSE for this point-forecast comparison. Variable-selection and temporal
attention weights can be extracted through `return_interpretation=True`. They
describe model weighting, not causation. WP5 does not publish business feature
importance; stability across independent seeds must be examined in WP6-WP8.

## Acceptance and resource recommendations

`configs/models.wp5.v1.json` freezes CPU float32, deterministic algorithms, two CPU
threads, batch size 256, AdamW, gradient clipping, validation checkpoint selection
and the resource caps. All samples are used, including incomplete final batches.
MoEBS and matched random searches for N-Space/TFT have the same search space,
maximum NFE, per-trial epochs, per-trial and total wall-clock caps. Algorithm
overhead, actual fit duration and parameter count may differ and are recorded.
MoEBS has an additional population/iteration cap. Equal NFE is checked before
claiming the three acceptance runs have matched budgets.

The runner uses train and validation only. Test tensor integrity is checked by
the upstream WP4 verifier but there is zero model prediction or metric on test.
Checkpoint reload must reproduce every validation prediction bit-for-bit in the
same runtime. Checkpoints support inference/reload; optimizer state and mid-epoch
resume are intentionally not implemented. Training records identify this limit.

Metrics use original pcs: MAE, RMSE, MAPE only for actual != 0 with count/coverage,
SMAPE=mean(200*abs(error)/max(abs(actual)+abs(prediction), epsilon)), and `r2` as
coefficient of determination. Epsilon is configured. MAPE on all-zero data and
R2 on a constant target return JSON null with coverage/defined indicators.

Training seconds include candidate fit and validation checkpoint selection, not
data preparation or optimizer search overhead. Search seconds and summed trial
training seconds are reported separately. Inference seconds cover adapter batch
conversion and forward execution with device synchronization, excluding feature
construction, file writing and final metric calculation. Acceptance has no CUDA
memory benchmark; hardware metadata probes CUDA capability and records CPU use.

WP6 should calibrate resource caps on the intended hardware, use the same planned
seed list (42, 123, 2026), and lock an official training/tuning protocol before
running it. The nine calendar weeks of validation constrain useful search size;
overlapping panel windows are not independent temporal observations. Do not
enlarge tuning based on test behaviour. Keep all splits/features/horizon fixed;
any later horizon study requires a separate shared version and three-model run.

## Commands

From the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m src.models.acceptance --config configs/models.wp5.v1.json
.\.venv\Scripts\python.exe -m src.models.acceptance --verify artifacts/runs/<run_id>/run_manifest.json
```

Every invocation creates unique private directories. The resolved pointer, split
manifest hash, full effective configuration, package versions, source snapshots,
hardware, timings, checkpoints, validation predictions and metrics are saved.
Original SAP inputs are never modified, and no data is uploaded to a service.
