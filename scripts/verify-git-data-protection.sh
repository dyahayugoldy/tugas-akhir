#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
exec python -m src.utils.git_protection
