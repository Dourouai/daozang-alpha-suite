#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

mkdir -p logs data/runtime data/decision_logs

if [ -f "config/local.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "config/local.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_HEALTHCHECK="${RUN_HEALTHCHECK:-true}"
RUN_POOL_REFRESH="${RUN_POOL_REFRESH:-false}"
RUN_TRADE_PLAN="${RUN_TRADE_PLAN:-true}"
RUN_FOCUS_CHECK="${RUN_FOCUS_CHECK:-false}"

notify_args=()
if [ -n "${FEISHU_WEBHOOK:-}" ]; then
  notify_args=(--notify feishu)
fi

run_step() {
  local name="$1"
  shift
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] START ${name}"
  "$@"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE ${name}"
}

if [ "$RUN_HEALTHCHECK" = "true" ]; then
  run_step "healthcheck" "$SCRIPT_DIR/server_healthcheck.sh"
fi

if [ "$RUN_POOL_REFRESH" = "true" ]; then
  run_step "daily-refresh-pool" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha daily-refresh-pool \
    --pool-size 50 \
    --scan-limit 120 \
    --profile config/profile_overrides.csv \
    --cycle balanced \
    --horizon ultra_short_2_3d \
    --min-market-cap 300 \
    --exclude-themes "消费,品牌消费" \
    --notify-title "北辰 Alpha 基础池刷新" \
    "${notify_args[@]}"
fi

if [ "$RUN_TRADE_PLAN" = "true" ]; then
  review_args=()
  if [ -n "${BEICHEN_REVIEW_DATE:-}" ]; then
    review_args=(--review-date "${BEICHEN_REVIEW_DATE}")
  fi
  run_step "trade-plan" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha trade-plan \
    --positions data/positions/current_positions.json \
    --watchlist data/watchlists/broad_target_pool_2026-07-03.txt \
    --model-scores ../daozang-alpha/data/exports/alpha_scores_latest.csv \
    --capital "${BEICHEN_CAPITAL:-10000}" \
    --top "${BEICHEN_TRADE_TOP:-3}" \
    "${review_args[@]}" \
    "${notify_args[@]}"
fi

if [ "$RUN_FOCUS_CHECK" = "true" ]; then
  run_step "focus-realtime" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha \
    --cycle balanced \
    --horizon ultra_short_2_3d \
    --profile config/profile_overrides.csv \
    --watchlist data/watchlists/current_focus_pool.txt \
    --limit 10 \
    --realtime \
    --notify-title "北辰 Alpha 重点池盘中检查" \
    "${notify_args[@]}"
fi
