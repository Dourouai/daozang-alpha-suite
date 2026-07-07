#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

mkdir -p logs data/runtime data/decision_logs
mkdir -p reports

if [ -f "config/local.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "config/local.env"
  set +a
fi

DEFAULT_PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  DEFAULT_PYTHON_BIN=".venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"
RUN_HEALTHCHECK="${RUN_HEALTHCHECK:-true}"
RUN_DAOZANG_SYNC_INDUSTRY_MAP="${RUN_DAOZANG_SYNC_INDUSTRY_MAP:-true}"
RUN_DAOZANG_SYNC_RISK_CALENDAR="${RUN_DAOZANG_SYNC_RISK_CALENDAR:-true}"
RUN_DAOZANG_SYNC_UNIVERSE="${RUN_DAOZANG_SYNC_UNIVERSE:-true}"
RUN_DAOZANG_SYNC_QLIB_BARS="${RUN_DAOZANG_SYNC_QLIB_BARS:-true}"
RUN_DAOZANG_EXPORT_BEICHEN_FEATURES="${RUN_DAOZANG_EXPORT_BEICHEN_FEATURES:-true}"
RUN_DAOZANG_BASELINE="${RUN_DAOZANG_BASELINE:-auto}"
RUN_DAOZANG_EXPORT_SCORES="${RUN_DAOZANG_EXPORT_SCORES:-true}"
RUN_POOL_REFRESH="${RUN_POOL_REFRESH:-false}"
RUN_TRADE_PLAN="${RUN_TRADE_PLAN:-true}"
RUN_FOCUS_CHECK="${RUN_FOCUS_CHECK:-false}"
RUN_OUTCOME_BACKFILL="${RUN_OUTCOME_BACKFILL:-true}"
RUN_STRATEGY_PERFORMANCE="${RUN_STRATEGY_PERFORMANCE:-true}"
BEICHEN_BROAD_WATCHLIST="${BEICHEN_BROAD_WATCHLIST:-data/watchlists/broad_target_pool_latest.txt}"
BEICHEN_TRADE_WATCHLIST="${BEICHEN_TRADE_WATCHLIST:-$BEICHEN_BROAD_WATCHLIST}"
BEICHEN_FEISHU_CHANNEL_MODE="${BEICHEN_FEISHU_CHANNEL_MODE:-trade_decisions}"
BEICHEN_FEISHU_SEND_POOL_REFRESH="${BEICHEN_FEISHU_SEND_POOL_REFRESH:-false}"
BEICHEN_FEISHU_SEND_FOCUS_CHECK="${BEICHEN_FEISHU_SEND_FOCUS_CHECK:-true}"
BEICHEN_FEISHU_SEND_STRATEGY_PERFORMANCE="${BEICHEN_FEISHU_SEND_STRATEGY_PERFORMANCE:-false}"
BEICHEN_STRATEGY_PERFORMANCE_REPORT="${BEICHEN_STRATEGY_PERFORMANCE_REPORT:-reports/strategy_performance_latest.txt}"
BEICHEN_STRATEGY_PERFORMANCE_MIN_SAMPLES="${BEICHEN_STRATEGY_PERFORMANCE_MIN_SAMPLES:-1}"
DAOZANG_UNIVERSE_LIMIT="${DAOZANG_UNIVERSE_LIMIT:-800}"
BEICHEN_BROAD_POOL_SIZE="${BEICHEN_BROAD_POOL_SIZE:-$DAOZANG_UNIVERSE_LIMIT}"
BEICHEN_BROAD_SCAN_LIMIT="${BEICHEN_BROAD_SCAN_LIMIT:-$DAOZANG_UNIVERSE_LIMIT}"
DAOZANG_BASELINE_QUICK="${DAOZANG_BASELINE_QUICK:-true}"
DAOZANG_NUM_BOOST_ROUND="${DAOZANG_NUM_BOOST_ROUND:-80}"
DAOZANG_EARLY_STOPPING_ROUNDS="${DAOZANG_EARLY_STOPPING_ROUNDS:-10}"
DAOZANG_QLIB_SYNC_WORKERS="${DAOZANG_QLIB_SYNC_WORKERS:-8}"
DAOZANG_QLIB_SYNC_TIMEOUT="${DAOZANG_QLIB_SYNC_TIMEOUT:-4}"
DAOZANG_BEICHEN_FEATURES_PATH="${DAOZANG_BEICHEN_FEATURES_PATH:-data/features/beichen_daily_features_latest.csv}"

trade_notify_args=()
pool_notify_args=()
focus_notify_args=()
strategy_notify_args=()
if [ -n "${FEISHU_WEBHOOK:-}" ] && [ "$BEICHEN_FEISHU_CHANNEL_MODE" != "off" ]; then
  trade_notify_args=(--notify feishu)
  if [ "$BEICHEN_FEISHU_CHANNEL_MODE" = "all" ] || [ "$BEICHEN_FEISHU_SEND_POOL_REFRESH" = "true" ]; then
    pool_notify_args=(--notify feishu)
  fi
  if [ "$BEICHEN_FEISHU_SEND_FOCUS_CHECK" != "false" ]; then
    focus_notify_args=(--notify feishu)
  fi
  if [ "$BEICHEN_FEISHU_CHANNEL_MODE" = "all" ] || [ "$BEICHEN_FEISHU_SEND_STRATEGY_PERFORMANCE" = "true" ]; then
    strategy_notify_args=(--notify feishu)
  fi
fi

run_step() {
  local name="$1"
  shift
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] START ${name}"
  "$@"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE ${name}"
}

run_optional_step() {
  local name="$1"
  shift
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] START ${name}"
  if "$@"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE ${name}"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN ${name} failed; continue without refreshed Daozang scores"
  fi
}

run_optional_step_in_dir() {
  local name="$1"
  local dir="$2"
  shift 2
  run_optional_step "$name" bash -c 'cd "$1" && shift && "$@"' bash "$dir" "$@"
}

python_has_daozang_research_deps() {
  "$DAOZANG_PYTHON_BIN" -c 'import lightgbm, qlib' >/dev/null 2>&1
}

latest_weekday_ymd() {
  "$PYTHON_BIN" -c 'from datetime import date, timedelta
d = date.today()
while d.weekday() >= 5:
    d -= timedelta(days=1)
print(d.strftime("%Y-%m-%d"))'
}

date_offset_ymd() {
  "$PYTHON_BIN" -c 'from datetime import date, timedelta
import sys
base = date.fromisoformat(sys.argv[1])
print((base + timedelta(days=int(sys.argv[2]))).strftime("%Y-%m-%d"))' "$1" "$2"
}

DAOZANG_TEST_END="${DAOZANG_TEST_END:-$(latest_weekday_ymd)}"
DAOZANG_TRAIN_START="${DAOZANG_TRAIN_START:-$(date_offset_ymd "$DAOZANG_TEST_END" -365)}"
DAOZANG_TRAIN_END="${DAOZANG_TRAIN_END:-$(date_offset_ymd "$DAOZANG_TEST_END" -120)}"
DAOZANG_VALID_START="${DAOZANG_VALID_START:-$(date_offset_ymd "$DAOZANG_TEST_END" -119)}"
DAOZANG_VALID_END="${DAOZANG_VALID_END:-$(date_offset_ymd "$DAOZANG_TEST_END" -44)}"
DAOZANG_TEST_START="${DAOZANG_TEST_START:-$(date_offset_ymd "$DAOZANG_TEST_END" -43)}"

DAOZANG_DIR="$PROJECT_DIR/../daozang-alpha"
DAOZANG_PYTHON_BIN="python3"
if [ -d "$DAOZANG_DIR" ]; then
  DAOZANG_DIR="$(cd "$DAOZANG_DIR" && pwd)"
  if [ -x "$DAOZANG_DIR/.venv/bin/python" ]; then
    DAOZANG_PYTHON_BIN="$DAOZANG_DIR/.venv/bin/python"
  fi
fi

if [ "$RUN_DAOZANG_SYNC_INDUSTRY_MAP" = "true" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    run_optional_step_in_dir "daozang-sync-industry-map" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha sync-akshare-industry-map \
      --target-universe data/universe/active_universe.csv
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without industry map"
  fi
fi

if [ "$RUN_DAOZANG_SYNC_RISK_CALENDAR" = "true" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    run_optional_step_in_dir "daozang-sync-risk-calendar" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha sync-akshare-risk-calendar \
      --target-universe data/universe/active_universe.csv \
      --as-of "$DAOZANG_TEST_END"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without risk calendar"
  fi
fi

if [ "$RUN_DAOZANG_SYNC_UNIVERSE" = "true" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    run_optional_step_in_dir "daozang-sync-universe" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha sync-beichen-universe \
      --beichen-root "$PROJECT_DIR" \
      --limit "$DAOZANG_UNIVERSE_LIMIT"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without active universe"
  fi
fi

if [ "$RUN_DAOZANG_SYNC_QLIB_BARS" = "true" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    run_optional_step_in_dir "daozang-sync-akshare-qlib-bars" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha sync-akshare-qlib-bars \
      --universe-file data/universe/active_universe.csv \
      --qlib-data-dir data/qlib/cn_data \
      --end "$DAOZANG_TEST_END" \
      --workers "$DAOZANG_QLIB_SYNC_WORKERS" \
      --request-timeout "$DAOZANG_QLIB_SYNC_TIMEOUT"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without Qlib bar refresh"
  fi
fi

if [ "$RUN_DAOZANG_EXPORT_BEICHEN_FEATURES" = "true" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    run_optional_step_in_dir "daozang-export-beichen-features" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha export-beichen-features \
      --beichen-root "$PROJECT_DIR" \
      --output "$DAOZANG_BEICHEN_FEATURES_PATH"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without Beichen daily features"
  fi
fi

if [ "$RUN_DAOZANG_BASELINE" != "false" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    if [ "$RUN_DAOZANG_BASELINE" = "true" ] || python_has_daozang_research_deps; then
      daozang_baseline_args=()
      if [ "$DAOZANG_BASELINE_QUICK" = "true" ]; then
        daozang_baseline_args+=(--quick)
      fi
      if [ -f "$DAOZANG_DIR/$DAOZANG_BEICHEN_FEATURES_PATH" ]; then
        daozang_baseline_args+=(--extra-features "$DAOZANG_BEICHEN_FEATURES_PATH")
      fi
      run_optional_step_in_dir "daozang-run-baseline" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha run-baseline \
        "${daozang_baseline_args[@]}" \
        --universe-file data/universe/active_universe.csv \
        --max-instruments "$DAOZANG_UNIVERSE_LIMIT" \
        --top-n "$DAOZANG_UNIVERSE_LIMIT" \
        --train-start "$DAOZANG_TRAIN_START" \
        --train-end "$DAOZANG_TRAIN_END" \
        --valid-start "$DAOZANG_VALID_START" \
        --valid-end "$DAOZANG_VALID_END" \
        --test-start "$DAOZANG_TEST_START" \
        --test-end "$DAOZANG_TEST_END" \
        --num-boost-round "$DAOZANG_NUM_BOOST_ROUND" \
        --early-stopping-rounds "$DAOZANG_EARLY_STOPPING_ROUNDS"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang research deps missing; skip baseline refresh"
    fi
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without refreshed Daozang scores"
  fi
fi

if [ "$RUN_DAOZANG_EXPORT_SCORES" = "true" ]; then
  if [ -d "$DAOZANG_DIR" ]; then
    run_optional_step_in_dir "daozang-export-scores" "$DAOZANG_DIR" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$DAOZANG_PYTHON_BIN" -m daozang_alpha export-scores
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN daozang-alpha directory missing; continue without refreshed Daozang scores"
  fi
fi

if [ "$RUN_HEALTHCHECK" = "true" ]; then
  run_step "healthcheck" "$SCRIPT_DIR/server_healthcheck.sh"
fi

if [ "$RUN_POOL_REFRESH" = "true" ]; then
  run_step "daily-refresh-pool" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha daily-refresh-pool \
    --pool-size "$BEICHEN_BROAD_POOL_SIZE" \
    --scan-limit "$BEICHEN_BROAD_SCAN_LIMIT" \
    --profile config/profile_overrides.csv \
    --cycle balanced \
    --horizon ultra_short_2_3d \
    --min-market-cap 300 \
    --exclude-themes "消费,品牌消费" \
    --notify-title "北辰 Alpha 基础池刷新" \
    "${pool_notify_args[@]}"
fi

if [ "$RUN_TRADE_PLAN" = "true" ]; then
  review_args=()
  if [ -n "${BEICHEN_REVIEW_DATE:-}" ]; then
    review_args=(--review-date "${BEICHEN_REVIEW_DATE}")
  fi
  run_step "trade-plan" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha trade-plan \
    --positions data/positions/current_positions.json \
    --watchlist "$BEICHEN_TRADE_WATCHLIST" \
    --model-scores ../daozang-alpha/data/exports/alpha_scores_latest.csv \
    --capital "${BEICHEN_CAPITAL:-10000}" \
    --top "${BEICHEN_TRADE_TOP:-3}" \
    "${review_args[@]}" \
    "${trade_notify_args[@]}"
fi

if [ "$RUN_FOCUS_CHECK" = "true" ]; then
  run_step "focus-realtime" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha \
    --cycle balanced \
    --horizon ultra_short_2_3d \
    --profile config/profile_overrides.csv \
    --watchlist data/watchlists/current_focus_pool.txt \
    --limit 10 \
    --realtime \
    --notify-title "北辰 Alpha 重点池盘中检查" \
    "${focus_notify_args[@]}"
fi

if [ "$RUN_OUTCOME_BACKFILL" = "true" ]; then
  run_optional_step "backfill-outcomes" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha backfill-outcomes \
    --horizons 1,3,5 \
    --quiet
fi

if [ "$RUN_STRATEGY_PERFORMANCE" = "true" ]; then
  run_optional_step "strategy-performance" env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON_BIN" -m beichen_alpha strategy-performance \
    --horizons 1,3,5 \
    --min-samples "$BEICHEN_STRATEGY_PERFORMANCE_MIN_SAMPLES" \
    --out "$BEICHEN_STRATEGY_PERFORMANCE_REPORT" \
    "${strategy_notify_args[@]}"
fi
