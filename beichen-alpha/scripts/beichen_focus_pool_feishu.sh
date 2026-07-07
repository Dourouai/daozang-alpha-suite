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
BEICHEN_FEISHU_SEND_FOCUS_CHECK="${BEICHEN_FEISHU_SEND_FOCUS_CHECK:-true}"

notify_args=()
if [ -n "${FEISHU_WEBHOOK:-}" ] && [ "$BEICHEN_FEISHU_CHANNEL_MODE" != "off" ] && [ "$BEICHEN_FEISHU_SEND_FOCUS_CHECK" != "false" ]; then
  notify_args=(--notify feishu)
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --profile config/profile_overrides.csv \
  --watchlist data/watchlists/current_focus_pool.txt \
  --limit 10 \
  --realtime \
  --notify-title "北辰 Alpha 重点池 10:00 分析" \
  "${notify_args[@]}"
