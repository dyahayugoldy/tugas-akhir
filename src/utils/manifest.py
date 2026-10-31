"""Create a synthetic acceptance run; never open SAP source files."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import random
import subprocess
import uuid

from jsonschema import Draft202012Validator, FormatChecker, ValidationError


ROOT = Path(__file__).resolve().parents[2]


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    # Exclusive creation fails rather than overwriting an artifact.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def validate_dummy_config(config):
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["config_version", "model_name", "seed", "data_mode", "synthetic_sample_count"],
        "properties": {
            "config_version": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
            "model_name": {"enum": ["moebs_transformer", "nspace_transformer", "tft"]},
            "seed": {"type": "integer", "minimum": 0},
            "data_mode": {"const": "synthetic-only"},
            "synthetic_sample_count": {"type": "integer", "minimum": 1, "maximum": 10000},
        },
    }
    Draft202012Validator(schema).validate(config)


def source_info(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    return git("rev-parse", "HEAD"), bool(git("status", "--porcelain", "--untracked-files=normal"))


def collect_environment():
    # Versions only: no environment variables, usernames, hostnames, or pip URLs.
    packages = {dist.metadata["Name"]: dist.version for dist in metadata.distributions()
                if dist.metadata["Name"]}
    return {
        "python_version": platform.python_version(),
        "packages": dict(sorted(packages.items())),
        "framework": "none", "framework_version": None, "device": "cpu",
        "hardware": {
            "os": platform.system() + " " + platform.release(),
            "architecture": platform.machine(),
            "cpu": platform.processor() or "unavailable",
            "logical_cpu_count": os.cpu_count(),
            "gpu": [],  # No GPU is used or probed by this metadata-only run.
        },
    }


def validate_manifest(manifest, root=ROOT, require_files=False):
    schema = read_json(root / "configs/run-manifest.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    # jsonschema's RFC3339 checker is an optional extra; validate calendar dates
    # explicitly as well so a minimal installation cannot silently skip them.
    try:
        datetime.fromisoformat(manifest["created_at_utc"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("created_at_utc is not a valid UTC timestamp") from error
    prefix = f"{manifest['model_name']}_{manifest['config_version']}_seed-{manifest['seed']}_"
    if not manifest["run_id"].startswith(prefix):
        raise ValueError("Run ID must include model, config version, and seed")
    run_dir = (root / "artifacts/runs" / manifest["run_id"]).resolve()
    if not run_dir.is_relative_to((root / "artifacts/runs").resolve()):
        raise ValueError("Run directory escapes artifacts/runs")
    for artifact in manifest["artifacts"].values():
        if artifact is None:
            continue
        parts = PurePosixPath(artifact).parts
        path = (root / artifact).resolve()
        if ".." in parts or not path.is_relative_to(run_dir) or path == run_dir:
            raise ValueError("Artifact must remain inside its own run directory")
        if require_files and not path.is_file():
            raise ValueError("Referenced artifact is missing")
    if manifest["run_type"] == "dummy":
        validate_dummy_config(manifest["effective_config"])
        for key in ("model_name", "config_version", "seed"):
            if manifest[key] != manifest["effective_config"][key]:
                raise ValueError("Manifest and effective config disagree")
    if require_files:
        saved_config = read_json(root / manifest["artifacts"]["effective_config"])
        if saved_config != manifest["effective_config"]:
            raise ValueError("Saved effective config differs from manifest")


def create_dummy_run(config, root=ROOT):
    validate_dummy_config(config)
    now = datetime.now(timezone.utc)
    run_id = (f"{config['model_name']}_{config['config_version']}_seed-{config['seed']}_"
              f"{now.strftime('%Y%m%dt%H%M%S%fz')}_{uuid.uuid4().hex[:12]}")
    revision, dirty = source_info(root)
    base = f"artifacts/runs/{run_id}"
    manifest = {
        "schema_version": "1", "run_type": "dummy", "run_id": run_id,
        "model_name": config["model_name"], "config_version": config["config_version"],
        "seed": config["seed"], "created_at_utc": now.isoformat().replace("+00:00", "Z"),
        "data_manifest_id": "synthetic-only", "split_manifest_id": "synthetic-only",
        "feature_contract_version": "synthetic-only", "source_revision": revision,
        "source_dirty": dirty, "effective_config": dict(config),
        "environment": collect_environment(),
        "timing": {"training_seconds": 0, "inference_seconds": 0},
        "artifacts": {
            "effective_config": f"{base}/config.resolved.json", "checkpoint": None,
            "predictions": None, "metrics": None, "dummy_output": f"{base}/dummy_output.json",
        },
    }
    validate_manifest(manifest, root)
    rng = random.Random(config["seed"])
    samples = [rng.random() for _ in range(config["synthetic_sample_count"])]
    output = {
        "data_mode": "synthetic-only", "sample_count": len(samples),
        "synthetic_sha256": hashlib.sha256(json.dumps(samples).encode("utf-8")).hexdigest(),
    }
    directory = (root / base).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise ValueError("Artifacts directory must remain inside the repository")
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "config.resolved.json", config)
    write_json(directory / "dummy_output.json", output)
    validate_manifest(manifest, root, require_files=True)
    write_json(directory / "run_manifest.json", manifest)
    return directory / "run_manifest.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    dummy = commands.add_parser("dummy")
    dummy.add_argument("--config", type=Path, default=ROOT / "configs/dummy-run.v1.json")
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "dummy":
        path = create_dummy_run(read_json(args.config))
        print("Validated synthetic dummy run: " + path.relative_to(ROOT).as_posix())
    else:
        validate_manifest(read_json(args.manifest), require_files=True)
        print("Run manifest and referenced artifacts are valid.")


if __name__ == "__main__":
    main()
