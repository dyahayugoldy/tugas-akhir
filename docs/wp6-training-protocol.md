# WP6 fair training protocol v1

Commands for training one model/seed at a time are documented in
[wp6-single-run-commands.md](wp6-single-run-commands.md).

WP6 is the official training stage, not an accuracy comparison and not the test
evaluation stage. It binds all three architectures to the same sealed WP4
manifest. Model fitting, selection and prediction use only train and validation.
The upstream integrity verifier may read test tensors to validate hashes/schema
and shared loaders; no model prediction or objective uses test in WP6.

## Frozen procedure

`configs/models.wp6.v1.json` specifies CPU float32, two CPU threads, batch size
256, AdamW/MSE, gradient clipping, shuffled full batches, and the shared seeds
42, 123 and 2026. Every training candidate and every final run creates a new
model and optimizer; no checkpoint or optimizer state is resumed.

Each fit permits at most 100 epochs. Validation RMSE is measured after every
epoch. With `min_delta=0.0`, an epoch improves only when its RMSE is strictly
lower than the prior best. Training stops after ten consecutive non-improving
epochs and restores the weights from the epoch with minimum validation RMSE.
The run record keeps the best epoch, completed epochs, per-epoch RMSE, update
time and validation-selection time.

Hyperparameter tuning is performed once per architecture with seed 2026 and
validation only. MoEBS and the two random-search comparators receive the same
four-evaluation limit, per-trial 100-epoch/3,600-second cap, 14,400-second
search cap, and shared search space. Trial attempts, including failures, count
toward the budget. The selected configuration is lexicographic validation RMSE,
validation MAE, parameter count, then trial id. Its tuning checkpoint is never
reused: its architecture and optimizer parameters are frozen, then nine new
final runs (three models by three seeds) begin independently.

Equal protocol limits do not require equal elapsed time or equal number of
epochs actually completed. Those are outcomes to report as efficiency results.
If a model cannot complete the common evaluation budget, the cohort verifier
fails rather than issuing a fairness claim.

Inference is benchmarked on the identical validation set after one warm-up and
five timed repetitions, including adapter conversion/forward execution but not
feature construction, file writing, or metric calculation. CUDA peak allocation
is recorded when CUDA is selected; CPU peak memory is recorded as unavailable
unless a validated platform-specific collector is added. Training update time,
validation-selection time, total elapsed time, parameter count, hardware,
package versions, source/config snapshots and checksums are preserved privately.

WP7 may load only the sealed final WP6 checkpoints to make the first test
predictions and compute holdout metrics. No test metric, prediction, or tuning
decision occurs in WP6.

## Completed cohort and historical resource deviations

WP6 was closed on 7 November 2026. The authoritative cohort, pinned member
manifests, audit reproduction commands and limitations are documented in
[reports/wp6-verification.md](../reports/wp6-verification.md). The membership
plan is `configs/cohort.wp6.v1.json`. Closure never rewrites historical manifests.

All three models have four candidates in the selected tuning cohort and three
fresh final runs. Historical retries consumed unequal physical compute. In the
TFT resume, trials 0-2 were copied byte-for-byte; the original fourth trial had
also completed and was recomputed with identical predictions. The audit includes
the donor evaluations in reconciled search cost and reports the extra physical
fit separately. Two excluded MoEBS searches and an excluded partial N-Space
search also consumed recorded compute. All their completed records reproduce
cohort candidates/predictions; they did not enlarge the selected candidate set.

Resource limits describe the selected protocol, not an assertion of equal total
historical compute. Recorded completed work, unmeasured interrupted overhead,
missing CPU peak memory and unmonitored host load are explicitly disclosed.
