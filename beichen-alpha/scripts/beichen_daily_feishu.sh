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

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m beichen_alpha \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --universe-limit 30 \
  --limit 5 \
  --disable-news \
  --disable-disclosures \
  --notify feishu \
  --notify-title "北辰 Alpha 09:30 候选池"
