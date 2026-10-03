# Reproducibility Protocol

## Scope

This protocol applies to every training or evaluation run for MoEBS-Transformer, N-Space Transformer, and TFT. It operationalizes the reproducibility requirements in `AGENTS.md` without storing SAP data in Git.

## Required manifest

Each run must write `artifacts/runs/<run_id>/run_manifest.json` and validate it against `configs/run-manifest.schema.json`.

`run_id` uses this pattern:

```text
<model>_<config-version>_seed-<seed>_<utc-timestamp>
```

Example:

```text
tft_v1_seed-42_20261003t091500z
```

The manifest records the immutable data/split identifiers, feature contract version, source revision, seed, environment, timing, and local artifact paths. It must not contain raw SAP rows, customer information, purchase order numbers, or secret values.

## Artifact policy

```text
artifacts/
  data/<data_manifest_id>/
  runs/<run_id>/
    run_manifest.json
    checkpoints/
    predictions.parquet
    metrics.json
    timing.json
```

`artifacts/` is intentionally ignored by Git. Reports that are safe to share may be placed under `reports/` only after anonymization review; detailed predictions and row-level extracts remain under `artifacts/`.

## Git data protection

- `dataset/`, `artifacts/`, `secrets/`, `.env*`, PEM files, and key files are ignored.
- `.githooks/pre-commit` rejects protected paths that were staged with `git add -f` or other bypasses.
- Configure this repository to use the hook once with `git config core.hooksPath .githooks`.
- Run `sh scripts/verify-git-data-protection.sh` before the first commit and in CI when available.

### Historical exposure remediation

Ignoring a path and removing it from the current index do not erase earlier Git commits. If confidential SAP data was already pushed, stop sharing the repository and perform an approved history rewrite followed by a force-push. Verify the rewritten remote contains no paths under `dataset/` before restoring access. This is a coordinated repository-administration action and must not be performed automatically by an experiment run.

## Dummy-run acceptance check

Before a real model run, generate a manifest using synthetic metadata only. The acceptance check passes when:

1. The manifest validates against the schema.
2. Every local result path begins with `artifacts/`.
3. `git status --ignored` shows SAP XLSX and generated artifacts as ignored.
4. `git ls-files` contains no path under `dataset/`, `artifacts/`, or `secrets/`.

The dummy-run generator will be implemented with the experiment runner in WP6; it must never read the SAP files.
