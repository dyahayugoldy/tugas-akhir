"""WP0 acceptance and failure-path tests use synthetic fixtures only."""

import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import ValidationError

from src.utils import git_protection, manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        shutil.copyfile(manifest.ROOT / "configs/run-manifest.schema.json",
                        self.root / "configs/run-manifest.schema.json")
        self.config = manifest.read_json(manifest.ROOT / "configs/dummy-run.v1.json")
        self.source = patch.object(manifest, "source_info", return_value=("a" * 40, False))
        self.source.start()
        self.addCleanup(self.source.stop)

    def make_run(self, config=None):
        return manifest.create_dummy_run(config or self.config, self.root)

    def test_valid_unique_runs_and_reproducible_seed(self):
        first = self.make_run()
        second = self.make_run()
        self.assertNotEqual(first, second)
        data = manifest.read_json(first)
        manifest.validate_manifest(data, self.root, require_files=True)
        self.assertEqual(manifest.read_json(first.parent / "dummy_output.json"),
                         manifest.read_json(second.parent / "dummy_output.json"))
        other = self.make_run({**self.config, "seed": self.config["seed"] + 1})
        self.assertNotEqual(manifest.read_json(first.parent / "dummy_output.json"),
                            manifest.read_json(other.parent / "dummy_output.json"))

    def test_does_not_open_dataset_or_modify_source(self):
        source = self.root / "dataset/source.xlsx"
        source.parent.mkdir()
        # Synthetic bytes, never real SAP content.
        source.write_bytes(b"synthetic read-only sentinel")
        before = source.stat().st_mtime_ns
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.is_relative_to(source.parent):
                raise AssertionError("Dummy run tried to open dataset")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            self.make_run()
        self.assertEqual(source.stat().st_mtime_ns, before)
        self.assertEqual(source.read_bytes(), b"synthetic read-only sentinel")

    def test_missing_required_metadata_and_bad_timestamp_rejected(self):
        data = manifest.read_json(self.make_run())
        for key in ("effective_config", "created_at_utc", "source_revision", "seed"):
            with self.subTest(key=key):
                changed = copy.deepcopy(data)
                del changed[key]
                with self.assertRaises(ValidationError):
                    manifest.validate_manifest(changed, self.root)
        for timestamp in ("invalidZ", "2026-02-30T10:00:00Z", "2026-10-31T25:00:00Z"):
            changed = copy.deepcopy(data)
            changed["created_at_utc"] = timestamp
            with self.assertRaises(ValidationError):
                manifest.validate_manifest(changed, self.root)
        del data["environment"]["packages"]
        with self.assertRaises(ValidationError):
            manifest.validate_manifest(data, self.root)

    def test_traversal_cross_run_and_missing_artifact_rejected(self):
        data = manifest.read_json(self.make_run())
        for path in ("artifacts/../dataset/source.xlsx",
                     f"artifacts/runs/{data['run_id']}/../other/output.json",
                     "artifacts/runs/other/output.json"):
            with self.subTest(path=path):
                changed = copy.deepcopy(data)
                changed["artifacts"]["dummy_output"] = path
                with self.assertRaises((ValueError, ValidationError)):
                    manifest.validate_manifest(changed, self.root)
        (self.root / data["artifacts"]["dummy_output"]).unlink()
        with self.assertRaises(ValueError):
            manifest.validate_manifest(data, self.root, require_files=True)

    def test_invalid_config_and_mismatched_saved_config_rejected(self):
        for config in ({**self.config, "data_mode": "sap"},
                       {**self.config, "seed": True},
                       {**self.config, "config_version": "../escape"},
                       {**self.config, "customer": "synthetic"}):
            with self.assertRaises(ValidationError):
                self.make_run(config)
        data = manifest.read_json(self.make_run())
        data["effective_config"]["synthetic_sample_count"] += 1
        with self.assertRaises(ValueError):
            manifest.validate_manifest(data, self.root, require_files=True)

    def test_experiment_requires_real_result_paths(self):
        data = manifest.read_json(self.make_run())
        data["run_type"] = "experiment"
        with self.assertRaises(ValidationError):
            manifest.validate_manifest(data, self.root)

    def test_write_does_not_overwrite(self):
        path = self.root / "result.json"
        manifest.write_json(path, {"first": True})
        with self.assertRaises(FileExistsError):
            manifest.write_json(path, {"second": True})
        self.assertEqual(manifest.read_json(path), {"first": True})


class GitProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "core.hooksPath", ".githooks")
        for name in (".gitignore", ".githooks/pre-commit", "src/__init__.py",
                     "src/utils/__init__.py", "src/utils/git_protection.py"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest.ROOT / name, destination)
        (self.root / ".githooks/pre-commit").chmod(0o755)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args],
                              capture_output=True, check=True)

    def stage(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture only\n", encoding="utf-8")
        self.git("add", "-f", "--", name)

    def test_ignored_and_clean_index_pass(self):
        git_protection.verify(self.root)

    def test_forced_add_blocked_and_removal_allowed(self):
        self.stage("dataset/synthetic.xlsx")
        with self.assertRaises(ValueError):
            git_protection.verify(self.root)
        with self.assertRaises(ValueError):
            git_protection.verify(self.root, staged_only=True)
        self.git("rm", "--cached", "--", "dataset/synthetic.xlsx")
        git_protection.verify(self.root)

    def test_nested_env_and_uppercase_checkpoint_blocked(self):
        for name in ("nested/.env.local", "nested/model.CKPT", "artifacts/result.json",
                     "logs/detail.txt", "copy.XLSX", "nested/private.KEY"):
            self.assertTrue(git_protection.is_protected(name), name)
        for name in (".env.example", "nested/.env.example", "reports/aggregate.csv",
                     "src/utils/manifest.py"):
            self.assertFalse(git_protection.is_protected(name), name)

    def test_missing_ignore_rule_rejected(self):
        (self.root / ".gitignore").write_text("dataset/\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            git_protection.verify(self.root)

    def test_actual_git_hook_blocks_forced_add(self):
        self.stage("dataset/synthetic.xlsx")
        result = subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=WP0 Test",
             "-c", "user.email=wp0@example.invalid", "commit", "-m", "synthetic fixture"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected path(s) in index", result.stderr)
        self.assertNotIn("synthetic.xlsx", result.stderr)

    def test_actual_git_hook_accepts_safe_file_and_env_example(self):
        self.stage("reports/safe.md")
        self.stage(".env.example")
        self.git("-c", "user.name=WP0 Test", "-c", "user.email=wp0@example.invalid",
                 "commit", "-m", "synthetic safe fixtures")


if __name__ == "__main__":
    unittest.main()
