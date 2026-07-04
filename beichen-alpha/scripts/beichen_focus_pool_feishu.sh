#!/bin/zsh
set -euo pipefail

cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/beichen-alpha

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

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m beichen_alpha \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --profile config/profile_overrides.csv \
  --watchlist data/watchlists/current_focus_pool.txt \
  --limit 10 \
  --realtime \
  --notify-title "北辰 Alpha 重点池 10:00 分析" \
  "${notify_args[@]}"
