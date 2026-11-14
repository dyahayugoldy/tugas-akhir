import copy
import unittest

import pandas as pd

from src.evaluation import comparative_decision as wp8


class ComparativeDecisionTests(unittest.TestCase):
    def config(self):
        return {
            "config_version": "comparison-wp8-v1", "purpose": "WP8_reproducible_comparative_decision",
            "evaluation_path": "artifacts/runs/example/evaluation.json", "evaluation_sha256": "a" * 64,
            "output_root": "artifacts/runs", "models": ["moebs_transformer", "nspace_transformer", "tft"],
            "seeds": [42, 123, 2026],
            "accuracy": {"primary_metrics": ["mae", "rmse", "smape", "r2"], "secondary_metrics": ["mape"],
                         "lower_is_better": ["mae", "rmse", "mape", "smape"], "higher_is_better": ["r2"],
                         "seed_variation_rule": "mean_gap_must_exceed_one_combined_sample_sd_to_call_a_stable_pairwise_advantage",
                         "combined_sd_multiplier": 1.0},
            "efficiency": {"metrics": ["parameter_count", "wp6_training_seconds", "test_inference_median_seconds"],
                           "lower_is_better": True, "training_time_scope": "fit", "inference_time_scope": "forward"},
            "interpretability": {"model_name": "tft", "split": "test", "batch_size": 256,
                                  "selection_groups": ["encoder_selection", "decoder_selection"],
                                  "minimum_fields_for_rank_stability": 3, "minimum_pairwise_spearman": 0.8,
                                  "publish_only_if_all_seed_pairs_meet_threshold": True, "disclosure": "contract_only"},
            "privacy": {"allow_raw_predictions": False, "allow_category_values": False,
                        "allow_sample_level_interpretation": False},
        }

    def rows(self):
        rows = []
        for model, base in (("moebs_transformer", 1.0), ("nspace_transformer", 2.0), ("tft", 3.0)):
            for seed, offset in zip((42, 123, 2026), (0.0, 0.1, -0.1)):
                rows.append({"model_name": model, "seed": seed, "count": 10, "mape_count": 2, "mape_coverage": 0.2,
                             "mae": base + offset, "rmse": base + offset, "mape": base + offset,
                             "smape": base + offset, "r2": 5.0 - base + offset, "parameter_count": base * 10,
                             "wp6_training_seconds": base * 20, "test_inference_median_seconds": base * 0.5})
        return pd.DataFrame(rows)

    def test_config_rejects_weaker_privacy_and_changed_grid(self):
        wp8.validate_config(self.config())
        changed = copy.deepcopy(self.config())
        changed["privacy"]["allow_raw_predictions"] = True
        with self.assertRaisesRegex(ValueError, "privacy"):
            wp8.validate_config(changed)
        changed = copy.deepcopy(self.config())
        changed["seeds"] = [42]
        with self.assertRaisesRegex(ValueError, "seed grid"):
            wp8.validate_config(changed)

    def test_stability_requires_every_seed_pair(self):
        stable = {42: {"fields": ["a", "b", "c"], "mean_weights": [0.1, 0.2, 0.7]},
                  123: {"fields": ["a", "b", "c"], "mean_weights": [0.2, 0.3, 0.8]},
                  2026: {"fields": ["a", "b", "c"], "mean_weights": [0.3, 0.4, 0.9]}}
        result = wp8.selection_stability(stable, threshold=0.8, minimum_fields=3)
        self.assertTrue(result["stable"])
        unstable = copy.deepcopy(stable)
        unstable[2026]["mean_weights"] = [0.9, 0.4, 0.1]
        self.assertFalse(wp8.selection_stability(unstable, threshold=0.8, minimum_fields=3)["stable"])

    def test_analysis_uses_seed_variation_before_single_winner(self):
        config = self.config()
        interpretation = {"publication_status": "no_feature_importance_exported_unstable_across_seeds"}
        analysis = wp8.analyze(self.rows(), config, interpretation)
        self.assertEqual(analysis["efficiency"]["single_efficiency_winner"], "moebs_transformer")
        self.assertEqual(analysis["decision"]["conditional_recommendation"], "moebs_transformer")
        self.assertTrue(analysis["accuracy"]["single_accuracy_winner"])


if __name__ == "__main__":
    unittest.main()
