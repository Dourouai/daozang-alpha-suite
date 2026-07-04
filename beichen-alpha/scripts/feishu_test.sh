#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"

if [ -f "config/local.env" ]; then
  set -a
  source "config/local.env"
  set +a
fi

if [ -z "${FEISHU_WEBHOOK:-}" ] || [[ "${FEISHU_WEBHOOK}" == *"replace-me"* ]]; then
  echo "FEISHU_WEBHOOK is not configured. Edit config/local.env first." >&2
  exit 2
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 - <<'PY'
from beichen_alpha.notifiers import send_text

response = send_text("北辰 Alpha 飞书测试：配置已生效。")
if isinstance(response, dict) and response.get("code") not in (None, 0):
    raise SystemExit(f"Feishu returned an error: {response}")
print("Feishu test message sent.")
PY
