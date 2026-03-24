#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_SRC="$SCRIPT_DIR/src"
PYTHON_BIN="${PYTHON:-python}"

# Runtime profile:
# - fast (default): only currently effective Rust paths.
# - all: enable all Rust toggles for testing.
# - off: disable all Rust toggles.
# - custom: keep caller-provided values as-is.
: "${BORG_RUST_PROFILE:=fast}"
case "$BORG_RUST_PROFILE" in
  fast)
    : "${BORG_RUST_PIPELINE:=1}"
    : "${BORG_RUST_COMPRESS:=1}"
    : "${BORG_RUST_ENCRYPT:=0}"
    : "${BORG_RUST_PIPELINE_COMBINED:=0}"
    : "${BORG_RUST_CHUNKER:=0}"
    ;;
  all)
    : "${BORG_RUST_PIPELINE:=1}"
    : "${BORG_RUST_COMPRESS:=1}"
    : "${BORG_RUST_ENCRYPT:=1}"
    : "${BORG_RUST_PIPELINE_COMBINED:=1}"
    : "${BORG_RUST_CHUNKER:=1}"
    ;;
  off)
    : "${BORG_RUST_PIPELINE:=0}"
    : "${BORG_RUST_COMPRESS:=0}"
    : "${BORG_RUST_ENCRYPT:=0}"
    : "${BORG_RUST_PIPELINE_COMBINED:=0}"
    : "${BORG_RUST_CHUNKER:=0}"
    ;;
  custom)
    : "${BORG_RUST_PIPELINE:=0}"
    : "${BORG_RUST_COMPRESS:=0}"
    : "${BORG_RUST_ENCRYPT:=0}"
    : "${BORG_RUST_PIPELINE_COMBINED:=0}"
    : "${BORG_RUST_CHUNKER:=0}"
    ;;
  *)
    echo "rorg.sh: invalid BORG_RUST_PROFILE=$BORG_RUST_PROFILE (expected: fast|all|off|custom)" >&2
    exit 2
    ;;
esac
export BORG_RUST_PIPELINE
export BORG_RUST_COMPRESS
export BORG_RUST_ENCRYPT
export BORG_RUST_PIPELINE_COMBINED
export BORG_RUST_CHUNKER

export PYTHONPATH="$REPO_SRC${PYTHONPATH:+:$PYTHONPATH}"
if [[ "${BORG_RUST_DEBUG_ENV:-0}" == "1" ]]; then
  echo "rorg profile=$BORG_RUST_PROFILE pipeline=$BORG_RUST_PIPELINE compress=$BORG_RUST_COMPRESS encrypt=$BORG_RUST_ENCRYPT combined=$BORG_RUST_PIPELINE_COMBINED chunker=$BORG_RUST_CHUNKER" >&2
fi
exec "$PYTHON_BIN" -m borg "$@"
