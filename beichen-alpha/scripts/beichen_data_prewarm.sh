#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

if [ -f "config/local.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "config/local.env"
  set +a
fi

if [ -x ".venv/bin/python" ]; then
  DEFAULT_PYTHON_BIN=".venv/bin/python"
else
  DEFAULT_PYTHON_BIN="python3"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

run_with_timeout() {
  local seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  else
    "$@"
  fi
}

mkdir -p data/runtime data/decision_logs logs

LOCK_FILE="${BEICHEN_PREWARM_LOCK_FILE:-data/runtime/data_prewarm.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] data-prewarm already running; skip."
  exit 0
fi

SOURCE="${BEICHEN_PREWARM_SOURCE:-qlib}"
QLIB_PROVIDER_URI="${BEICHEN_QLIB_PROVIDER_URI:-../daozang-alpha/data/qlib/cn_data}"
if [ "$SOURCE" = "qlib" ] && [ ! -d "$QLIB_PROVIDER_URI" ]; then
  SOURCE="baostock"
fi

WATCHLIST="${BEICHEN_TRADE_WATCHLIST:-data/watchlists/trade_target_pool_latest.txt}"
if [ ! -f "$WATCHLIST" ]; then
  WATCHLIST="${BEICHEN_BROAD_WATCHLIST:-data/watchlists/broad_target_pool_latest.txt}"
fi

POSITIONS="${BEICHEN_POSITIONS:-data/positions/current_positions.json}"
MODEL_SCORES="${BEICHEN_MODEL_SCORES:-../daozang-alpha/data/exports/alpha_scores_latest.csv}"
CAPITAL="${BEICHEN_CAPITAL:-10000}"
TOP="${BEICHEN_TRADE_TOP:-3}"
OUTPUT="${BEICHEN_PREWARM_TRADE_PLAN_TEXT:-data/runtime/latest_trade_plan.txt}"
STATUS_JSON="${BEICHEN_PREWARM_STATUS_JSON:-data/runtime/latest_data_prewarm.json}"
TIMEOUT_SECONDS="${BEICHEN_PREWARM_TIMEOUT_SECONDS:-240}"
FULL_FACTORS="${BEICHEN_PREWARM_FULL_FACTORS:-false}"
RUN_FACTORS="${BEICHEN_PREWARM_FACTORS:-true}"
FACTOR_TIMEOUT_SECONDS="${BEICHEN_PREWARM_FACTOR_TIMEOUT_SECONDS:-240}"
GLOBAL_FACTOR_TIMEOUT_SECONDS="${BEICHEN_PREWARM_GLOBAL_FACTOR_TIMEOUT_SECONDS:-90}"
FLOW_FACTOR_TIMEOUT_SECONDS="${BEICHEN_PREWARM_FLOW_FACTOR_TIMEOUT_SECONDS:-180}"
SENTIMENT_FACTOR_TIMEOUT_SECONDS="${BEICHEN_PREWARM_SENTIMENT_FACTOR_TIMEOUT_SECONDS:-180}"
FACTOR_LIMIT="${BEICHEN_PREWARM_FACTOR_LIMIT:-12}"
FACTOR_STATUS_JSON="${BEICHEN_PREWARM_FACTOR_STATUS_JSON:-data/runtime/latest_factor_prewarm.json}"
GLOBAL_FACTOR_STATUS_JSON="${BEICHEN_PREWARM_GLOBAL_FACTOR_STATUS_JSON:-data/runtime/latest_factor_prewarm_global.json}"
FLOW_FACTOR_STATUS_JSON="${BEICHEN_PREWARM_FLOW_FACTOR_STATUS_JSON:-data/runtime/latest_factor_prewarm_flow.json}"
SENTIMENT_FACTOR_STATUS_JSON="${BEICHEN_PREWARM_SENTIMENT_FACTOR_STATUS_JSON:-data/runtime/latest_factor_prewarm_sentiment.json}"
QLIB_FALLBACK="${BEICHEN_PREWARM_QLIB_FALLBACK:-true}"

extra_args=()
if [ "$SOURCE" = "qlib" ]; then
  extra_args+=(--source qlib --qlib-provider-uri "$QLIB_PROVIDER_URI")
else
  extra_args+=(--source baostock)
fi

if [ "$FULL_FACTORS" != "true" ]; then
  extra_args+=(
    --disable-flow-factor
    --disable-global-linkage
    --disable-sentiment
    --disable-advanced
  )
fi

tmp_output="$(mktemp data/runtime/latest_trade_plan.XXXXXX.tmp)"
tmp_error="$(mktemp data/runtime/latest_trade_plan.XXXXXX.err)"
started_at="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[$started_at] START data-prewarm source=$SOURCE watchlist=$WATCHLIST"

set +e
run_with_timeout "$TIMEOUT_SECONDS" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha trade-plan \
  --positions "$POSITIONS" \
  --watchlist "$WATCHLIST" \
  --model-scores "$MODEL_SCORES" \
  --capital "$CAPITAL" \
  --top "$TOP" \
  --decision-log data/decision_logs/recommendations.jsonl \
  --notify none \
  "${extra_args[@]}" \
  >"$tmp_output" 2>"$tmp_error"
exit_code=$?
set -e

if [ "$exit_code" -ne 0 ] && [ "$SOURCE" = "qlib" ] && [ "$QLIB_FALLBACK" = "true" ]; then
  first_error_tail="$(tail -n 20 "$tmp_error" 2>/dev/null | tr '\n' ' ' | sed 's/"/\\"/g')"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN qlib prewarm failed; fallback to baostock error=$first_error_tail" >&2
  rm -f "$tmp_output" "$tmp_error"
  tmp_output="$(mktemp data/runtime/latest_trade_plan.XXXXXX.tmp)"
  tmp_error="$(mktemp data/runtime/latest_trade_plan.XXXXXX.err)"
  SOURCE="baostock"
  extra_args=(--source baostock)
  if [ "$FULL_FACTORS" != "true" ]; then
    extra_args+=(
      --disable-flow-factor
      --disable-global-linkage
      --disable-sentiment
      --disable-advanced
    )
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] START data-prewarm fallback source=$SOURCE watchlist=$WATCHLIST"
  set +e
  run_with_timeout "$TIMEOUT_SECONDS" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha trade-plan \
    --positions "$POSITIONS" \
    --watchlist "$WATCHLIST" \
    --model-scores "$MODEL_SCORES" \
    --capital "$CAPITAL" \
    --top "$TOP" \
    --decision-log data/decision_logs/recommendations.jsonl \
    --notify none \
    "${extra_args[@]}" \
    >"$tmp_output" 2>"$tmp_error"
  exit_code=$?
  set -e
fi

finished_at="$(date '+%Y-%m-%d %H:%M:%S')"
if [ "$exit_code" -eq 0 ]; then
  mv "$tmp_output" "$OUTPUT"
  rm -f "$tmp_error"
  status="ok"
  echo "[$finished_at] DONE data-prewarm output=$OUTPUT"
else
  error_tail="$(tail -n 20 "$tmp_error" 2>/dev/null | tr '\n' ' ' | sed 's/"/\\"/g')"
  rm -f "$tmp_output" "$tmp_error"
  status="failed"
  echo "[$finished_at] WARN data-prewarm failed exit=$exit_code error=$error_tail" >&2
fi

factor_status="skipped"
factor_exit_code=0
factor_results=()
run_factor_source() {
  local name="$1"
  local timeout_seconds="$2"
  local status_path="$3"
  shift 3
  local source_started_at
  source_started_at="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$source_started_at] START factor-prewarm/$name watchlist=$WATCHLIST limit=$FACTOR_LIMIT"
  set +e
  run_with_timeout "$timeout_seconds" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha prewarm-factors \
    --positions "$POSITIONS" \
    --watchlist "$WATCHLIST" \
    --limit "$FACTOR_LIMIT" \
    --status-json "$status_path" \
    "$@"
  local source_exit_code=$?
  set -e
  if [ "$source_exit_code" -eq 0 ]; then
    factor_results+=("$name:ok:$source_exit_code:$status_path")
  else
    factor_results+=("$name:failed:$source_exit_code:$status_path")
    if [ "$factor_exit_code" -eq 0 ]; then
      factor_exit_code="$source_exit_code"
    fi
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE factor-prewarm/$name exit=$source_exit_code"
}

if [ "$RUN_FACTORS" = "true" ]; then
  run_factor_source "global" "$GLOBAL_FACTOR_TIMEOUT_SECONDS" "$GLOBAL_FACTOR_STATUS_JSON" --disable-flow --disable-sentiment
  run_factor_source "flow" "$FLOW_FACTOR_TIMEOUT_SECONDS" "$FLOW_FACTOR_STATUS_JSON" --disable-global --disable-sentiment
  run_factor_source "sentiment" "$SENTIMENT_FACTOR_TIMEOUT_SECONDS" "$SENTIMENT_FACTOR_STATUS_JSON" --disable-flow --disable-global
  if [ "$factor_exit_code" -eq 0 ]; then
    factor_status="ok"
  else
    factor_status="partial"
  fi
  "$PYTHON_BIN" - "$FACTOR_STATUS_JSON" "$factor_status" "$factor_exit_code" "${factor_results[@]}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
sources = []
for raw in sys.argv[4:]:
    name, status, exit_code, status_json = raw.split(":", 3)
    sources.append(
        {
            "name": name,
            "status": status,
            "exit_code": int(exit_code),
            "status_json": status_json,
        }
    )
payload = {
    "status": sys.argv[2],
    "exit_code": int(sys.argv[3]),
    "sources": sources,
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE factor-prewarm status=$factor_status exit=$factor_exit_code"
fi

"$PYTHON_BIN" - "$STATUS_JSON" "$status" "$exit_code" "$started_at" "$finished_at" "$SOURCE" "$WATCHLIST" "$OUTPUT" "$factor_status" "$factor_exit_code" "$FACTOR_STATUS_JSON" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "status": sys.argv[2],
    "exit_code": int(sys.argv[3]),
    "started_at": sys.argv[4],
    "finished_at": sys.argv[5],
    "source": sys.argv[6],
    "watchlist": sys.argv[7],
    "output": sys.argv[8],
    "factor_status": sys.argv[9],
    "factor_exit_code": int(sys.argv[10]),
    "factor_status_json": sys.argv[11],
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

exit "$exit_code"
