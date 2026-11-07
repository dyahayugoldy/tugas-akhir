# Reproducibility Protocol

## Scope

This protocol applies to every training or evaluation run for MoEBS-Transformer, N-Space Transformer, and TFT. It operationalizes the reproducibility requirements in `AGENTS.md` without storing SAP data in Git.

## Required manifest

Each run must write `artifacts/runs/<run_id>/run_manifest.json` and validate it against `configs/run-manifest.schema.json`.

`run_id` uses this pattern:

```text
<model>_<config-version>_seed-<seed>_<utc-timestamp>_<unique-suffix>
```

Example:

```text
tft_wp0-v1_seed-42_20261031t050000000000z_a1b2c3d4e5f6
```

Schema version `1` requires the run type, immutable data/split identifiers, feature contract version, source revision and dirty flag, effective configuration, seed, UTC timestamp, installed package versions, Python/framework version, CPU/GPU metadata, training/inference seconds, and local artifact paths. It must not contain raw SAP rows, customer information, purchase order numbers, environment variables, hostnames, or secret values.

Dummy runs use `run_type: dummy`, synthetic-only identifiers, `framework: none`, CPU metadata, an empty GPU list (no GPU used or probed), zero training/inference seconds, and null checkpoint/prediction/metric paths. These are acceptance checks, not trained models or experimental results. Experiment manifests require checkpoint, prediction, and metric paths; WP6 will populate them from real training and inference.

Every artifact path must resolve inside its own `artifacts/runs/<run_id>/` directory; traversal and cross-run paths are rejected. Files and run directories are created exclusively, so an existing run cannot be overwritten. The validator also checks that referenced files exist and the saved configuration matches the manifest. UTC date validation does not depend on optional `jsonschema` format extras.

`source_dirty: true` means the recorded commit alone cannot reproduce local source changes. Commit reviewed source/configuration before official experiments; package versions are recorded per run but hardware and numerical determinism must be verified in WP6. The dummy fixture uses a local seeded RNG; only its sample count and checksum are saved. Equal seed/configuration produces the same checksum in the same Python environment; timestamps and run IDs intentionally differ.

## Artifact policy

```text
artifacts/
  data/<data_manifest_id>/
  runs/<run_id>/
    config.resolved.json
    run_manifest.json
    checkpoints/
    predictions.parquet
    metrics.json
    timing.json
    dummy_output.json  # dummy runs only; no training artifacts
```

`artifacts/` is intentionally ignored by Git. Reports that are safe to share may be placed under `reports/` only after anonymization review; detailed predictions and row-level extracts remain under `artifacts/`.

Keep raw inputs read-only: WP0 never opens or modifies SAP workbooks. Later ingestion must open them only for reading. Keep canonical transactions, aggregated panels, feature tensors, and split indexes in separate subdirectories under `artifacts/data/<data_manifest_id>/`; never overwrite a previous version. Use new manifest IDs when transformations change. Checkpoints, detailed predictions, logs, and metrics remain under the corresponding run directory. See `reports/README.md` for the disclosure review record required before tracking aggregate reports.

## WP1 canonical data

WP1 is reproduced from the active verified cleaning manifest with:

```powershell
python -m src.data.harmonize_workbooks --config configs/data-harmonization.v1.json
```

The command verifies source and cleaned-input checksums before and after reading. It writes a new private directory under `artifacts/data/canonical/` containing `canonical_transactions.csv.gz`, `schema_report.json`, `data_quality_report.json`, and `data_manifest.json`. Every source row is retained with a `valid`, `flagged`, or `rejected` quality status; no imputation, row deletion, currency conversion, or feature selection occurs in WP1. Review the generated manifest and then deliberately update `configs/current-data.json` before a later stage consumes a new canonical run.

## WP3 weekly panel and causal features

```powershell
python -m src.data.weekly_panel --config configs/panel-weekly.v1.json
python -m unittest discover -s tests -v
```

The command reads the ready WP2 manifest from `configs/current-data.json`, verifies its output hashes and original-source lineage, and creates a new private run under `artifacts/data/weekly_panel/`. It never updates the active pointer automatically. Verify the new manifest and then update `active_panel_manifest` and its associated paths deliberately; retain the WP1/WP2 pointers.

The run separates observed aggregates (`aggregated/observed_weekly.csv.gz`), calendar-complete labels (`panel/panel_weekly.csv.gz`), identity/knowledge-time registry (`panel/series_registry.csv.gz`), and causal features (`features/calendar_lags.csv.gz`). Exact decimal target sums are reconciled both for all observed transactions and for complete panel weeks. Partial boundary-week observations remain in the observed audit. Feature CSVs do not include labels, identifiers from SAP transactions, operational status, or monetary covariates.

`target_eda.json`, `panel_report.json`, `window_design.json`, `feature_contract.json`, resolved configuration, the input pointer snapshot, and snapshots/hashes of the four Python source dependencies accompany every run. All detailed category labels and numeric summaries remain private. Identical configuration/input/code produces identical hashes for deterministic data and report outputs; run IDs, manifests, and elapsed time intentionally differ.

No minimum-length filter based on future observations is applied. Series start at their first observed week and continue through the last complete coverage week. Eligibility requires encoder plus lag history at each origin. The feature accessor requires the original forecast origin and registry, masks unavailable future lag values, and excludes series not yet known at that origin. WP4 must fit vocabularies/scalers on train only, preserve a common split, and carry forward the declared annual-coverage and zero-capture-delay assumptions. Annual snapshots cannot prove absence of late/backdated revisions.

## Git data protection

- `dataset/`, `artifacts/`, `secrets/`, `.env*` (except `.env.example`), PEM/key files, workbooks, checkpoints, detailed logs, and Parquet files are ignored. Extension checks include uppercase copies.
- `.githooks/pre-commit` rejects protected paths that were staged with `git add -f` or other bypasses.
- Configure this repository to use the hook once with `git config core.hooksPath .githooks`.
- Run `python -m src.utils.git_protection` in PowerShell, CMD, or a POSIX shell before committing and in CI when available. `sh scripts/verify-git-data-protection.sh` is a wrapper for the same check.
- The hook calls the same Python utility with `--staged`, processes NUL-delimited filenames, and fails if Python/checks fail. Git for Windows supplies the hook shell; `sh` does not need to be on the PowerShell PATH. `.gitattributes` preserves LF for hooks and shell scripts.
- Checks reject protected paths forced into the index, including nested credential files and sensitive extension copies. They report counts rather than confidential filenames. `.env.example` is allowed but must contain placeholders only. Ignore rules and hooks do not inspect arbitrary file contents, replace disclosure review, prevent `--no-verify`, or erase Git history.

### Historical exposure remediation

Ignoring a path and removing it from the current index do not erase earlier Git commits. If confidential SAP data was already pushed, stop sharing the repository and perform an approved history rewrite followed by a force-push. Verify the rewritten remote contains no paths under `dataset/` before restoring access. This is a coordinated repository-administration action and must not be performed automatically by an experiment run.

## Dummy-run acceptance check

Before a real model run, generate a manifest using synthetic metadata only. The acceptance check passes when:

1. The manifest validates against the schema.
2. Every local result path begins with `artifacts/`.
3. `git status --ignored` shows SAP XLSX and generated artifacts as ignored.
4. `git ls-files` contains no path under `dataset/`, `artifacts/`, or `secrets/`.

From the repository root, using Python 3.12 (the verified environment):

```powershell
python -m pip install -r requirements.txt
git config core.hooksPath .githooks
python -m unittest discover -s tests -v
python -m src.utils.manifest dummy --config configs/dummy-run.v1.json
python -m src.utils.git_protection
git status --ignored --short
```

The dummy command prints its unique `run_manifest.json` path. Revalidate that path with:

```powershell
python -m src.utils.manifest validate artifacts/runs/<run_id>/run_manifest.json
```

The dummy configuration only accepts model, configuration version, seed, synthetic mode, and synthetic sample count; no SAP path or business metadata is accepted. `model_name` is a naming check and does not instantiate or train that architecture. Tests use temporary repositories and synthetic fixtures to verify deterministic checksums, unique/exclusive outputs, required metadata, valid timestamps, artifact isolation, no dataset access, and real Git-hook allow/block behavior without staging anything in this repository. Acceptance evidence is recorded in `reports/wp0-verification.md`.
