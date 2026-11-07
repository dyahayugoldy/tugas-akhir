"""End-to-end WP4 -> WP5 acceptance with synthetic lineage, no SAP input."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from src.data.harmonize_workbooks import ROOT, sha256_file
from src.features.split_windows import run as window_run
from src.models.acceptance import run, validate_config, verify_acceptance
from src.models.adapter import ModelData
from src.utils.manifest import write_json
import test_split_windows as wp4_fixture


class AcceptanceTests(unittest.TestCase):
    def test_three_model_artifacts_same_binding_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = wp4_fixture.SplitWindowTests()
            fixture.setUp()
            config_path = fixture.workspace(root)
            windows = window_run(config_path, root=root)
            pointer_path = root / "configs/current-data.json"
            # Replace this synthetic pointer only; real current-data is never edited.
            pointer_path.unlink()
            write_json(pointer_path, {"active_split_manifest": (windows / "split_manifest.json").relative_to(root).as_posix(),
                                      "active_data_manifest": "artifacts/synthetic-canonical/data_manifest.json"})
            (root / "docs").mkdir()
            for name in ("requirements.txt", "configs/run-manifest.schema.json", "docs/wp5-models.md"):
                shutil.copyfile(ROOT / name, root / name)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                            "commit", "-q", "--allow-empty", "-m", "Synthetic acceptance"], check=True, capture_output=True)
            config = json.loads((ROOT / "configs/models.wp5.v1.json").read_text())
            config["training"]["batch_size"] = 4
            config["search"]["max_evaluations"] = 2
            config["search"]["space"]["d_model"]["values"] = [8]
            config["search"]["space"]["n_heads"]["values"] = [1, 2]
            for name, architecture in config["models"].items():
                architecture.update(d_model=8, dropout=0.0, embedding_dim=2)
                if name != "tft":
                    architecture.update(d_ff=16, encoder_layers=1, decoder_layers=1)
            model_config = root / "configs/models.wp5.v1.json"
            write_json(model_config, config)
            before = sha256_file(windows / "split_manifest.json")
            manifests = run(model_config, root=root)
            self.assertEqual(len(manifests), 3)
            verified = [verify_acceptance(path, root=root) for path in manifests]
            self.assertEqual({m["split_manifest_id"] for m in verified}, {windows.name})
            self.assertEqual({m["wp5"]["search_evaluations"] for m in verified}, {2})
            self.assertEqual(len({m["wp5"]["verification"]["shared_numpy_digest"]["train"] for m in verified}), 1)
            self.assertEqual(sha256_file(windows / "split_manifest.json"), before)
            self.assertTrue(all(m["wp5"]["verification"]["test_predictions"] == 0 for m in verified))
            self.assertEqual(ModelData(windows / "split_manifest.json", "tft", "validation").contract["horizon_weeks"], 1)
            (manifests[0].parent / "metrics.json").write_bytes(b"synthetic corruption")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_acceptance(manifests[0], root=root)

    def test_acceptance_config_rejects_official_training_and_test_objectives(self):
        config = json.loads((ROOT / "configs/models.wp5.v1.json").read_text())
        validate_config(config)
        config["training"]["max_epochs"] = 100
        with self.assertRaisesRegex(ValueError, "one-epoch"):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
