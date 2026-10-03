#!/bin/sh
set -eu

for protected_path in \
  "dataset/VAL 2023.XLSX" \
  "dataset/VAL 2024.XLSX" \
  "dataset/VAL 2025.XLSX" \
  "artifacts/example.parquet" \
  ".env"; do
  if ! git check-ignore -q "$protected_path"; then
    echo "Expected ignored path: $protected_path" >&2
    exit 1
  fi
done

tracked_protected=$(git ls-files -- dataset artifacts secrets .env '*.pem' '*.key')
if [ -n "$tracked_protected" ]; then
  echo "Protected paths are still tracked:" >&2
  printf '%s\n' "$tracked_protected" >&2
  exit 1
fi

echo "Git data-protection checks passed."
