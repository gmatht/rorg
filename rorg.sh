#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_SRC="$SCRIPT_DIR/src"
PYTHON_BIN="${PYTHON:-python}"

export BORG_RUST_PIPELINE=1
export BORG_RUST_COMPRESS=1
export BORG_RUST_ENCRYPT=1
export BORG_RUST_PIPELINE_COMBINED=1
export BORG_RUST_CHUNKER=1

export PYTHONPATH="$REPO_SRC${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m borg "$@"
