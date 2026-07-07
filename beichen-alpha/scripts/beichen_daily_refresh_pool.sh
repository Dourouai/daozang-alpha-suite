#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p logs

if [ -f "config/local.env" ]; then
  set -a
  source "config/local.env"
  set +a
fi

BEICHEN_FEISHU_CHANNEL_MODE="${BEICHEN_FEISHU_CHANNEL_MODE:-trade_decisions}"
BEICHEN_FEISHU_SEND_POOL_REFRESH="${BEICHEN_FEISHU_SEND_POOL_REFRESH:-false}"

notify_args=()
if [ -n "${FEISHU_WEBHOOK:-}" ] && { [ "$BEICHEN_FEISHU_CHANNEL_MODE" = "all" ] || [ "$BEICHEN_FEISHU_SEND_POOL_REFRESH" = "true" ]; }; then
  notify_args=(--notify feishu)
else
  echo "Feishu skipped: pool refresh is data maintenance, not a direct trading decision." >&2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha daily-refresh-pool \
  --pool-size 50 \
  --scan-limit 120 \
  --profile config/profile_overrides.csv \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --min-market-cap 300 \
  --exclude-themes "消费,品牌消费" \
  --notify-title "北辰 Alpha 基础池收盘刷新" \
  "${notify_args[@]}" \
  > logs/daily_refresh_pool.out.log \
  2> logs/daily_refresh_pool.err.log
