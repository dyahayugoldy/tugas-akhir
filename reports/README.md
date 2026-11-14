# Safe aggregate reports

Only anonymized aggregate tables and figures reviewed for disclosure may be
tracked here. Detailed predictions, identifiers, raw transactions, checkpoints,
and logs belong under the ignored `artifacts/` directory.

Before publishing a report, record its source run IDs, aggregation, reviewer,
review date, and disclosure decision. Check titles, labels, tooltips, metadata,
and filenames as well as visible cells. Small groups or combinations that reveal
customers, purchase orders, or individual transaction values must be suppressed
or combined. Never copy data directly from `dataset/` into this directory.

WP0 verification evidence is recorded in `wp0-verification.md`.
WP2 business-filter verification and outstanding decisions are recorded in `wp2-verification.md`.
WP3 weekly-panel, regularization, causal-feature, and design verification is recorded in `wp3-verification.md`; detailed EDA and category labels remain private.
WP4 chronological splits, train-only preprocessing, shared windows, and three-consumer data-contract verification are recorded in `wp4-verification.md`; tensors, encoders, sample identities, and source snapshots remain private.
Removal of obsolete preprocessing datasets and retention of active lineage/audit evidence are recorded in `preprocessing-cleanup.md`.
WP5 three-model PyTorch implementation, matched one-epoch acceptance, checkpoint reload and causal contract tests are recorded in `wp5-verification.md`; detailed validation predictions, metrics, trial archives and weights remain private. These runs are implementation evidence, not final model comparisons.
WP6 completed three-model multi-seed training, certified cohort, historical resume provenance, retry resource accounting and limitations are recorded in `wp6-verification.md`; per-seed epochs/timing are in `wp6-training-verification.md`. WP6 does not evaluate test data; its final checkpoints are evaluated in WP7.

WP7 completed evaluation of nine final checkpoints on identical sealed test windows is recorded in `wp7-verification.md`, including per-seed and aggregate metrics, MAPE coverage, residual/sparse-series analysis, negative forecasts, inference timing and limitations. Detailed forecasts, category labels and error diagnostics remain private. WP8 owns the final comparative decision.

WP8 uses the sealed WP7 receipt only. Its reproducible decision package stays in `artifacts/runs/`; the reviewed aggregate conclusion is recorded in `wp8-verification.md`. TFT variable-selection summaries are published only when all configured seed-pair stability checks pass; detailed per-sample weights remain private.
