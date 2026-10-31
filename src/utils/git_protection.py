"""Check Git ignores/index without reading or printing confidential file contents."""

import argparse
from pathlib import Path, PurePosixPath
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PROTECTED_DIRS = {"dataset", "artifacts", "secrets", "logs", "mlruns", "wandb", "tensorboard"}
PROTECTED_SUFFIXES = {".xlsx", ".xls", ".parquet", ".pt", ".pth", ".ckpt", ".onnx",
                      ".safetensors", ".log", ".pem", ".key"}
IGNORE_PROBES = (
    "dataset/VAL 2023.XLSX", "dataset/VAL 2024.XLSX", "dataset/VAL 2025.XLSX",
    "artifacts/probe.json", "secrets/probe.txt", ".env", ".env.local",
    "nested/.env.local", "probe.pem", "probe.key", "probe.XLSX",
    "probe.parquet", "probe.ckpt", "probe.log", "logs/probe.txt",
)


def is_protected(path):
    path = PurePosixPath(path.replace("\\", "/"))
    name = path.name.lower()
    return (any(part.lower() in PROTECTED_DIRS for part in path.parts[:-1])
            or path.suffix.lower() in PROTECTED_SUFFIXES
            or (name != ".env.example" and (name == ".env" or name.startswith(".env."))))


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def index_paths(root, staged_only=False):
    args = (["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
            if staged_only else ["ls-files", "-z"])
    return [path.decode("utf-8", errors="surrogateescape")
            for path in git(root, *args).split(b"\0") if path]


def verify(root=ROOT, staged_only=False):
    blocked = [path for path in index_paths(root, staged_only) if is_protected(path)]
    if blocked:
        # Do not leak customer/PO information that might occur in a filename.
        raise ValueError(f"Git data protection failed: {len(blocked)} protected path(s) in index")
    if staged_only:
        return
    for probe in IGNORE_PROBES:
        result = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-q", probe],
                                capture_output=True)
        if result.returncode != 0:
            raise ValueError("Git data protection failed: a required ignore rule is missing")
    if git(root, "config", "--get", "core.hooksPath").decode().strip() != ".githooks":
        raise ValueError("Configure the hook with: git config core.hooksPath .githooks")
    if not (root / ".githooks/pre-commit").is_file():
        raise ValueError("Pre-commit hook is missing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Check only newly staged paths")
    args = parser.parse_args()
    try:
        verify(staged_only=args.staged)
    except (ValueError, subprocess.CalledProcessError) as error:
        # CalledProcessError contains command arguments only, not source data.
        parser.exit(1, str(error) + "\n")
    print("Git data-protection checks passed.")


if __name__ == "__main__":
    main()
