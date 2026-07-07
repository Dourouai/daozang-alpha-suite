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
if [[ -n "${FEISHU_WEBHOOK:-}" && ( "${BEICHEN_FEISHU_CHANNEL_MODE:-trade_decisions}" == "all" || "${BEICHEN_FEISHU_SEND_DAILY_CANDIDATES:-false}" == "true" ) ]]; then
  notify_args=(--notify feishu)
else
  echo "Feishu skipped: daily candidate pool is data maintenance, not a direct trading decision." >&2
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m beichen_alpha \
  --cycle balanced \
  --horizon ultra_short_2_3d \
  --universe-limit 30 \
  --limit 5 \
  --disable-news \
  --disable-disclosures \
  "${notify_args[@]}" \
  --notify-title "北辰 Alpha 09:30 候选池"
