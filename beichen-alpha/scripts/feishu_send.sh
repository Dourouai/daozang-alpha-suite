#!/bin/zsh
set -euo pipefail

cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/beichen-alpha

if [ -f "config/local.env" ]; then
  set -a
  source "config/local.env"
  set +a
fi

if [ -z "${FEISHU_WEBHOOK:-}" ]; then
  echo "FEISHU_WEBHOOK is not configured. Edit config/local.env first." >&2
  exit 2
fi

if [ "$#" -eq 0 ]; then
  echo "Usage: ./scripts/feishu_send.sh \"message text\"" >&2
  exit 2
fi

FEISHU_MESSAGE="$*" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 - <<'PY'
import os

from beichen_alpha.notifiers import send_text

response = send_text(os.environ["FEISHU_MESSAGE"])
if isinstance(response, dict) and response.get("code") not in (None, 0):
    raise SystemExit(f"Feishu returned an error: {response}")
print("Feishu message sent.")
PY
