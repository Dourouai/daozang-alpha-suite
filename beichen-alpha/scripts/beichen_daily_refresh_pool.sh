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

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m beichen_alpha daily-refresh-pool \
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
