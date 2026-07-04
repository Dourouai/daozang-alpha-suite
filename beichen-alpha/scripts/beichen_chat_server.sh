#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

if [ -f "config/local.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "config/local.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha chat-server \
  --host "${FEISHU_CHAT_HOST:-127.0.0.1}" \
  --port "${FEISHU_CHAT_PORT:-8787}"
