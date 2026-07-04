#!/bin/zsh
set -euo pipefail

cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/beichen-alpha

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p logs

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m beichen_alpha \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --profile config/profile_overrides.csv \
  --watchlist data/watchlists/current_focus_pool.txt \
  --limit 10 \
  --realtime \
  --disable-news \
  --disable-disclosures \
  --notify none \
  --quiet > logs/focus_seed.out.log 2> logs/focus_seed.err.log
