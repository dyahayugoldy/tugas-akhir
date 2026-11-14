"""WP8 comparison from one sealed WP7 evaluation package.

This module never fits models or rebuilds data.  It checks the frozen WP7
package, aggregates only per-seed outcomes, and reloads TFT checkpoints solely
to measure whether their already-trained selection weights are stable.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
from pathlib import Path
import shutil
import statistics
import uuid

import numpy as np
import pandas as pd
import torch

from src.data.harmonize_workbooks import ROOT, load_json, relative_path, sha256_file
from src.models.adapter import ForecastAdapter, ModelData
from src.utils.manifest import source_info, write_json
from . import test_evaluation as wp7


STAGE = "WP8_comparative_decision"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_path(root, name):
    path = relative_path(root, name, description="WP8 private evidence")
    allowed = (root / "artifacts/runs").resolve()
    require(path.is_relative_to(allowed), "WP8 evidence must remain inside artifacts/runs")
    return path


def validate_config(config):
    required = {"config_version", "purpose", "evaluation_path", "evaluation_sha256", "output_root", "models", "seeds",
                "accuracy", "efficiency", "interpretability", "privacy"}
    require(set(config) == required, "WP8 config fields differ from the approved contract")
    require(config["config_version"] == "comparison-wp8-v1" and config["purpose"] == "WP8_reproducible_comparative_decision",
            "Unexpected WP8 config identity")
    require(config["output_root"] == "artifacts/runs", "WP8 output must be private")
    require(config["models"] == ["moebs_transformer", "nspace_transformer", "tft"], "WP8 model grid differs from WP7")
    require(config["seeds"] == [42, 123, 2026], "WP8 seed grid differs from WP7")
    require(len(config["evaluation_sha256"]) == 64, "WP8 requires a pinned WP7 receipt checksum")
    accuracy = config["accuracy"]
    require(set(accuracy) == {"primary_metrics", "secondary_metrics", "lower_is_better", "higher_is_better",
                              "seed_variation_rule", "combined_sd_multiplier"}, "Invalid WP8 accuracy policy")
    declared = set(accuracy["primary_metrics"] + accuracy["secondary_metrics"])
    require(declared == {"mae", "rmse", "mape", "smape", "r2"}, "WP8 must retain all five WP7 metrics")
    require(set(accuracy["lower_is_better"]) == {"mae", "rmse", "mape", "smape"}
            and accuracy["higher_is_better"] == ["r2"]
            and np.isfinite(accuracy["combined_sd_multiplier"]) and accuracy["combined_sd_multiplier"] > 0,
            "Invalid WP8 metric direction or seed-variation threshold")
    efficiency = config["efficiency"]
    require(set(efficiency) == {"metrics", "lower_is_better", "training_time_scope", "inference_time_scope"}
            and efficiency["metrics"] == ["parameter_count", "wp6_training_seconds", "test_inference_median_seconds"]
            and efficiency["lower_is_better"] is True, "Invalid WP8 efficiency policy")
    interpretation = config["interpretability"]
    require(set(interpretation) == {"model_name", "split", "batch_size", "selection_groups", "minimum_fields_for_rank_stability",
                                    "minimum_pairwise_spearman", "publish_only_if_all_seed_pairs_meet_threshold", "disclosure"},
            "Invalid WP8 interpretability policy")
    require(interpretation["model_name"] == "tft" and interpretation["split"] == "test"
            and interpretation["batch_size"] > 0 and interpretation["selection_groups"] == ["encoder_selection", "decoder_selection"]
            and interpretation["minimum_fields_for_rank_stability"] >= 3
            and 0 < interpretation["minimum_pairwise_spearman"] <= 1
            and interpretation["publish_only_if_all_seed_pairs_meet_threshold"] is True,
            "WP8 interpretability must use the frozen TFT test predictions only")
    require(config["privacy"] == {"allow_raw_predictions": False, "allow_category_values": False,
                                  "allow_sample_level_interpretation": False}, "WP8 privacy policy was weakened")


def read_evidence(config, root):
    receipt_path = private_path(root, config["evaluation_path"])
    require(sha256_file(receipt_path) == config["evaluation_sha256"], "Pinned WP7 receipt checksum differs")
    receipt = wp7.verify(receipt_path, root=root)
    require(receipt["stage"] == "WP7_test_evaluation" and receipt["status"] == "complete", "WP7 is not complete")
    pairs = {(entry["model_name"], entry["seed"]) for entry in receipt["runs"]}
    expected = set(itertools.product(config["models"], config["seeds"]))
    require(pairs == expected and len(receipt["runs"]) == len(expected), "WP7 grid differs from the configured WP8 grid")
    base = receipt_path.parent
    per_seed_path, summary_path = base / "per-seed.csv", base / "model-summary.csv"
    require(sha256_file(per_seed_path) == receipt["checksums"]["per-seed.csv"], "WP7 per-seed table changed")
    require(sha256_file(summary_path) == receipt["checksums"]["model-summary.csv"], "WP7 summary table changed")
    per_seed, summary = pd.read_csv(per_seed_path), pd.read_csv(summary_path)
    require(set(per_seed.model_name) == set(config["models"]) and len(per_seed) == len(expected), "WP7 per-seed rows are incomplete")
    require(per_seed.groupby("model_name").seed.nunique().to_dict() == {name: len(config["seeds"]) for name in config["models"]},
            "WP7 model seed counts differ")
    require(per_seed["count"].nunique() == 1 and per_seed["mape_count"].nunique() == 1
            and per_seed["mape_coverage"].nunique() == 1, "WP7 models did not share evaluation coverage")
    return receipt_path, receipt, per_seed, summary


def as_native(value):
    if isinstance(value, dict):
        return {str(key): as_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_native(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def descriptive_rows(per_seed, models, columns):
    rows = []
    for model in models:
        subset = per_seed.loc[per_seed.model_name.eq(model)]
        row = {"model_name": model, "seed_count": int(len(subset))}
        for column in columns:
            values = subset[column].astype(float).to_numpy()
            require(np.isfinite(values).all() and len(values) >= 2, f"Invalid per-seed {column} values")
            row[column] = {"mean": float(values.mean()), "std_ddof1": float(values.std(ddof=1)),
                           "min": float(values.min()), "max": float(values.max())}
        rows.append(row)
    return rows


def rank_models(rows, metric, *, lower_is_better):
    ordered = sorted(rows, key=lambda row: (row[metric]["mean"], row["model_name"]), reverse=not lower_is_better)
    return [row["model_name"] for row in ordered]


def pairwise_metric_advantages(rows, metric, *, lower_is_better, multiplier):
    output = []
    for left, right in itertools.combinations(rows, 2):
        left_mean, right_mean = left[metric]["mean"], right[metric]["mean"]
        difference = (right_mean - left_mean) if lower_is_better else (left_mean - right_mean)
        # A positive difference means the left model has the preferable mean.
        combined = float(np.hypot(left[metric]["std_ddof1"], right[metric]["std_ddof1"]))
        if difference == 0:
            favored = None
        else:
            favored = left["model_name"] if difference > 0 else right["model_name"]
        output.append({"metric": metric, "models": [left["model_name"], right["model_name"]],
                       "favored_by_mean": favored, "preferred_mean_gap": abs(float(difference)),
                       "combined_seed_sd": combined, "threshold": multiplier * combined,
                       "stable_advantage": bool(abs(difference) > multiplier * combined)})
    return output


def ordinal_ranks(values):
    values = np.asarray(values, dtype=np.float64)
    require(values.ndim == 1 and len(values) >= 2 and np.isfinite(values).all(), "Invalid importance vector")
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and values[order[stop]] == values[order[start]]:
            stop += 1
        ranks[order[start:stop]] = (start + stop - 1) / 2
        start = stop
    return ranks


def spearman(left, right):
    a, b = ordinal_ranks(left), ordinal_ranks(right)
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def selection_stability(seed_weights, *, threshold, minimum_fields):
    seeds = sorted(seed_weights)
    require(len(seeds) >= 2, "At least two TFT seeds are required for interpretability stability")
    fields = seed_weights[seeds[0]]["fields"]
    require(len(fields) >= minimum_fields, "Too few fields for rank-stability assessment")
    for seed in seeds:
        require(seed_weights[seed]["fields"] == fields, "TFT field contracts differ across seeds")
    pairs = []
    for left, right in itertools.combinations(seeds, 2):
        correlation = spearman(seed_weights[left]["mean_weights"], seed_weights[right]["mean_weights"])
        pairs.append({"seeds": [int(left), int(right)], "spearman": correlation,
                      "meets_threshold": correlation is not None and correlation >= threshold})
    means = np.mean([seed_weights[seed]["mean_weights"] for seed in seeds], axis=0)
    return {"fields": list(fields), "seed_pairs": pairs, "minimum_pairwise_spearman": threshold,
            "stable": bool(all(pair["meets_threshold"] for pair in pairs)),
            "mean_weights": [float(value) for value in means]}


def average_tft_interpretation(receipt, config, root):
    policy = config["interpretability"]
    entries = sorted((entry for entry in receipt["runs"] if entry["model_name"] == policy["model_name"]),
                     key=lambda entry: entry["seed"])
    require([entry["seed"] for entry in entries] == config["seeds"], "TFT WP7 entries differ from configured seeds")
    weights = {group: {} for group in policy["selection_groups"]}
    attention = {}
    for entry in entries:
        manifest_path = private_path(root, entry["manifest_path"])
        manifest = load_json(manifest_path)
        effective = manifest["effective_config"]
        split_path = relative_path(root, effective["split_manifest_path"], description="WP8 TFT split")
        data = ModelData(split_path, "tft", policy["split"])
        require(data.contract == effective["data_contract"], "TFT data contract differs from WP7 record")
        model = ForecastAdapter.load(manifest_path.parent / "checkpoint.pt", expected_contract=data.contract,
                                     runtime=effective["runtime"])
        require(model.fit_record["test_used"] is False, "TFT checkpoint was trained on test data")
        sums, counts = {}, {}
        with torch.inference_mode():
            for batch in data.batches(policy["batch_size"]):
                inputs = model.tensor_inputs(batch["inputs"])
                _, explanation = model.network(inputs, return_interpretation=True)
                for group in policy["selection_groups"]:
                    value = explanation[{"encoder_selection": "encoder_weights", "decoder_selection": "decoder_weights"}[group]]
                    require(torch.isfinite(value).all() and torch.allclose(value.sum(-1), torch.ones_like(value[..., 0]), atol=1e-6),
                            "Invalid TFT variable-selection weights")
                    reduced = value.detach().cpu().numpy().astype(np.float64)
                    sums[group] = sums.get(group, np.zeros(reduced.shape[-1], dtype=np.float64)) + reduced.sum(axis=tuple(range(reduced.ndim - 1)))
                    counts[group] = counts.get(group, 0) + int(np.prod(reduced.shape[:-1]))
                value = explanation["temporal_attention"]
                require(torch.isfinite(value).all() and torch.allclose(value.sum(-1), torch.ones_like(value[..., 0]), atol=1e-6),
                        "Invalid TFT temporal attention")
                reduced = value.detach().cpu().numpy().astype(np.float64)
                sums["temporal_attention"] = sums.get("temporal_attention", np.zeros(reduced.shape[-1], dtype=np.float64)) + reduced.sum(axis=tuple(range(reduced.ndim - 1)))
                counts["temporal_attention"] = counts.get("temporal_attention", 0) + int(np.prod(reduced.shape[:-1]))
        encoder_fields = ["observed:" + field for field in data.contract["encoder_observed"]] + ["known:" + field for field in data.contract["encoder_known"]]
        decoder_fields = ["lag:" + field for field in data.contract["decoder_lags"]] + ["known:" + field for field in data.contract["decoder_known"]]
        attention_fields = [f"encoder_week_{offset}" for offset in range(-data.contract["encoder_weeks"], 0)] + ["decoder_step_1"]
        fields = {"encoder_selection": encoder_fields, "decoder_selection": decoder_fields, "temporal_attention": attention_fields}
        for group, field_names in fields.items():
            require(len(sums[group]) == len(field_names) and counts[group] > 0, "TFT interpretation shape differs from contract")
            average = sums[group] / counts[group]
            require(np.isfinite(average).all() and np.isclose(average.sum(), 1.0, atol=1e-8), "TFT averaged importance is invalid")
            if group in policy["selection_groups"]:
                weights[group][entry["seed"]] = {"fields": field_names, "mean_weights": [float(value) for value in average],
                                                   "observation_count": counts[group]}
            else:
                attention[entry["seed"]] = {"fields": field_names, "mean_weights": [float(value) for value in average],
                                              "observation_count": counts[group]}
    summaries = {group: selection_stability(seed_values, threshold=policy["minimum_pairwise_spearman"],
                                             minimum_fields=policy["minimum_fields_for_rank_stability"])
                 for group, seed_values in weights.items()}
    temporal = selection_stability(attention, threshold=policy["minimum_pairwise_spearman"],
                                   minimum_fields=policy["minimum_fields_for_rank_stability"])
    publishable = [group for group, result in summaries.items() if result["stable"]]
    return {"model_name": "tft", "split": policy["split"], "method": "mean_post_training_variable_selection_weights",
            "causal_interpretation": False, "selection": summaries, "temporal_attention": temporal,
            "published_selection_groups": publishable,
            "publication_status": "stable_groups_only" if publishable else "no_feature_importance_exported_unstable_across_seeds",
            "per_seed_selection_weights_private": weights}


def analyze(per_seed, config, interpretability):
    accuracy_policy, efficiency_policy = config["accuracy"], config["efficiency"]
    all_metrics = accuracy_policy["primary_metrics"] + accuracy_policy["secondary_metrics"]
    accuracy_rows = descriptive_rows(per_seed, config["models"], all_metrics)
    efficiency_rows = descriptive_rows(per_seed, config["models"], efficiency_policy["metrics"])
    lower = set(accuracy_policy["lower_is_better"])
    rankings = {metric: rank_models(accuracy_rows, metric, lower_is_better=metric in lower) for metric in all_metrics}
    pairwise = {metric: pairwise_metric_advantages(accuracy_rows, metric, lower_is_better=metric in lower,
                                                   multiplier=accuracy_policy["combined_sd_multiplier"])
                for metric in all_metrics}
    primary_leaders = {metric: rankings[metric][0] for metric in accuracy_policy["primary_metrics"]}
    leader_counts = {model: sum(leader == model for leader in primary_leaders.values()) for model in config["models"]}
    accuracy_front_runner = max(config["models"], key=lambda model: (leader_counts[model], -config["models"].index(model)))
    stable_primary_pairs = [item for metric in accuracy_policy["primary_metrics"] for item in pairwise[metric]]
    single_accuracy_winner = (len(set(primary_leaders.values())) == 1
                              and all(item["stable_advantage"] for item in stable_primary_pairs
                                      if item["favored_by_mean"] == accuracy_front_runner))
    efficiency_rankings = {metric: rank_models(efficiency_rows, metric, lower_is_better=True)
                           for metric in efficiency_policy["metrics"]}
    efficiency_leaders = {metric: rank[0] for metric, rank in efficiency_rankings.items()}
    efficiency_winner = next(iter(set(efficiency_leaders.values()))) if len(set(efficiency_leaders.values())) == 1 else None
    conditional = efficiency_winner if efficiency_winner == accuracy_front_runner and leader_counts[efficiency_winner] >= 2 else None
    return {"data_scope": {"count_per_seed": int(per_seed["count"].iloc[0]), "mape_count_per_seed": int(per_seed["mape_count"].iloc[0]),
                            "mape_coverage": float(per_seed["mape_coverage"].iloc[0]), "unit": "pcs"},
            "accuracy": {"per_model": accuracy_rows, "rankings_by_mean": rankings, "pairwise_seed_variation": pairwise,
                         "primary_leaders_by_mean": primary_leaders, "single_accuracy_winner": single_accuracy_winner,
                         "accuracy_front_runner": accuracy_front_runner},
            "efficiency": {"per_model": efficiency_rows, "rankings_by_mean": efficiency_rankings,
                           "single_efficiency_winner": efficiency_winner,
                           "scope": {"training": efficiency_policy["training_time_scope"], "inference": efficiency_policy["inference_time_scope"]}},
            "interpretability": interpretability,
            "decision": {"single_overall_winner": None,
                         "conditional_recommendation": conditional,
                         "recommendation_rule": "recommend_only_when_the_same_model_leads_efficiency_and_at_least_two_primary_accuracy_means",
                         "reason": ("accuracy_tradeoffs_or_seed_variation_prevent_a_single_winner"
                                    if not single_accuracy_winner else "single_accuracy_winner_still_requires_efficiency_review")}}


def source_snapshot(root, base, config_path):
    paths = [Path(__file__).resolve(), root / "src/evaluation/test_evaluation.py", root / "src/models/adapter.py",
             root / "src/models/networks.py", config_path]
    digests = {}
    for source in paths:
        source = source.resolve()
        relative = source.relative_to(root).as_posix()
        target = base / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        digests[relative] = sha256_file(target)
    return digests


def output_checksums(base):
    return {path.relative_to(base).as_posix(): sha256_file(path) for path in sorted(base.rglob("*"))
            if path.is_file() and path.name != "decision.json"}


def run(config_path, *, root=ROOT):
    root, config_path = Path(root).resolve(), Path(config_path).resolve()
    config = load_json(config_path)
    validate_config(config)
    receipt_path, receipt, per_seed, _ = read_evidence(config, root)
    interpretability = average_tft_interpretation(receipt, config, root)
    analysis = analyze(per_seed, config, interpretability)
    now = datetime.now(timezone.utc)
    base = private_path(root, config["output_root"] + f"/wp8-comparison_{now:%Y%m%dt%H%M%Sz}_{uuid.uuid4().hex[:8]}")
    base.mkdir(parents=True, exist_ok=False)
    write_json(base / "config.resolved.json", config)
    write_json(base / "comparison.json", as_native(analysis))
    sources = source_snapshot(root, base, config_path)
    revision, dirty = source_info(root)
    receipt = {"stage": STAGE, "status": "complete", "created_at_utc": now.isoformat().replace("+00:00", "Z"),
               "config_version": config["config_version"], "source_revision": revision, "source_dirty": dirty,
               "wp7_evaluation_path": receipt_path.relative_to(root).as_posix(), "wp7_evaluation_sha256": sha256_file(receipt_path),
               "models": config["models"], "seeds": config["seeds"], "source_code_sha256": sources,
               "privacy": config["privacy"], "checksums": output_checksums(base)}
    write_json(base / "decision.json", receipt)
    # Full verification is deliberately a separate CLI action.  It replays the
    # costly sealed WP7 verifier, so separating it keeps each command within a
    # bounded runtime without weakening the artifact checks.
    print(f"WP8 comparative decision created: {(base / 'decision.json').relative_to(root).as_posix()}", flush=True)
    return base / "decision.json"


def verify(path, *, root=ROOT):
    root, path = Path(root).resolve(), Path(path).resolve()
    private_path(root, path.relative_to(root).as_posix())
    receipt = load_json(path)
    require(receipt["stage"] == STAGE and receipt["status"] == "complete", "Incomplete WP8 decision")
    base = path.parent
    config = load_json(base / "config.resolved.json")
    validate_config(config)
    require(receipt["config_version"] == config["config_version"] and receipt["models"] == config["models"]
            and receipt["seeds"] == config["seeds"] and receipt["privacy"] == config["privacy"], "WP8 receipt/config differs")
    require(receipt["checksums"] == output_checksums(base), "WP8 output checksum mismatch")
    for name, digest in receipt["source_code_sha256"].items():
        require(sha256_file(base / "source" / name) == digest and sha256_file(root / name) == digest,
                "WP8 source changed since the decision")
    receipt_path, wp7_receipt, per_seed, _ = read_evidence(config, root)
    require(receipt_path.relative_to(root).as_posix() == receipt["wp7_evaluation_path"]
            and sha256_file(receipt_path) == receipt["wp7_evaluation_sha256"], "WP8 WP7 binding differs")
    interpretation = average_tft_interpretation(wp7_receipt, config, root)
    expected = as_native(analyze(per_seed, config, interpretation))
    require(expected == load_json(base / "comparison.json"), "WP8 comparison no longer reproduces")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.config:
        run(args.config)
    else:
        verify(args.verify)
        print("WP8 comparison, WP7 lineage, and TFT stability checks verified.", flush=True)


if __name__ == "__main__":
    main()
