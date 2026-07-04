#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p logs

if [ -f "config/local.env" ]; then
  set -a
  source "config/local.env"
  set +a
fi

notify_args=()
if [ -n "${FEISHU_WEBHOOK:-}" ]; then
  notify_args=(--notify feishu)
fi

review_args=()
if [ -n "${BEICHEN_REVIEW_DATE:-}" ]; then
  review_args=(--review-date "${BEICHEN_REVIEW_DATE}")
fi

DEFAULT_PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  DEFAULT_PYTHON_BIN=".venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha trade-plan \
  --positions data/positions/current_positions.json \
  --watchlist data/watchlists/broad_target_pool_2026-07-03.txt \
  --model-scores ../daozang-alpha/data/exports/alpha_scores_latest.csv \
  --capital 10000 \
  --top 3 \
  "${review_args[@]}" \
  "${notify_args[@]}"
