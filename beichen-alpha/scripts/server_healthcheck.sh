#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

mkdir -p logs data/runtime data/decision_logs data/positions

if [ -f "config/local.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "config/local.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha healthcheck \
  --require-feishu \
  "$@"
