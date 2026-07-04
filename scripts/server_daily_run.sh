#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
SUITE_DIR="${SCRIPT_DIR:h}"

exec "${SUITE_DIR}/beichen-alpha/scripts/server_daily_run.sh" "$@"
