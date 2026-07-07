#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/www/wwwroot/daozang-alpha-suite}"
BRANCH="${BRANCH:-main}"
REPO_URL="${REPO_URL:-https://github.com/Dourouai/daozang-alpha-suite.git}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_DAOZANG_RESEARCH="${INSTALL_DAOZANG_RESEARCH:-false}"
RUN_DEPLOY_HEALTHCHECK="${RUN_DEPLOY_HEALTHCHECK:-false}"
SKIP_GIT_UPDATE="${SKIP_GIT_UPDATE:-false}"
ENABLE_CHAT_SERVICE="${ENABLE_CHAT_SERVICE:-true}"
ENABLE_DAILY_TIMER="${ENABLE_DAILY_TIMER:-true}"
ENABLE_OPTIONAL_TIMERS="${ENABLE_OPTIONAL_TIMERS:-false}"
ENABLE_CHECK0945_TIMER="${ENABLE_CHECK0945_TIMER:-$ENABLE_OPTIONAL_TIMERS}"
ENABLE_FOCUS0955_TIMER="${ENABLE_FOCUS0955_TIMER:-$ENABLE_OPTIONAL_TIMERS}"
ENABLE_FOCUS1000_TIMER="${ENABLE_FOCUS1000_TIMER:-$ENABLE_OPTIONAL_TIMERS}"
ENABLE_POSITION1030_TIMER="${ENABLE_POSITION1030_TIMER:-$ENABLE_OPTIONAL_TIMERS}"
ENABLE_INTRADAY_MONITOR_TIMER="${ENABLE_INTRADAY_MONITOR_TIMER:-$ENABLE_OPTIONAL_TIMERS}"
ENABLE_POOL1540_TIMER="${ENABLE_POOL1540_TIMER:-$ENABLE_OPTIONAL_TIMERS}"
ENABLE_DATA_PREWARM_TIMER="${ENABLE_DATA_PREWARM_TIMER:-$ENABLE_OPTIONAL_TIMERS}"

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

enable_timer_if() {
  local enabled="$1"
  local timer="$2"
  if [ "$enabled" = "true" ]; then
    run_root systemctl enable --now "$timer"
  else
    echo "Skip enabling $timer (set corresponding ENABLE_*_TIMER=true to enable)." >&2
  fi
}

if [ "$SKIP_GIT_UPDATE" != "true" ]; then
  if [ ! -d "$APP_DIR/.git" ]; then
    run_root mkdir -p "$APP_DIR"
    if [ ! -w "$APP_DIR" ]; then
      run_root chown "$(id -u):$(id -g)" "$APP_DIR"
    fi
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi

  cd "$APP_DIR"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  echo "Skip git update because SKIP_GIT_UPDATE=true; assuming code has already been synced to $APP_DIR." >&2
fi

cd "$APP_DIR/beichen-alpha"
if [ -x ".venv/bin/python" ] && ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf .venv
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[data,content,server]"

mkdir -p data/positions data/decision_logs data/runtime logs

if [ ! -f "config/local.env" ]; then
  cp config/local.env.example config/local.env
  echo "Created beichen-alpha/config/local.env. Fill Feishu secrets before production use." >&2
fi

if [ ! -f "data/positions/current_positions.json" ] && [ -f "data/positions/current_positions.example.json" ]; then
  cp data/positions/current_positions.example.json data/positions/current_positions.json
  echo "Created data/positions/current_positions.json from example. Review holdings before production use." >&2
fi

if [ "$INSTALL_DAOZANG_RESEARCH" = "true" ]; then
  cd "$APP_DIR/daozang-alpha"
  if [ -x ".venv/bin/python" ] && ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    rm -rf .venv
  fi
  if [ ! -x ".venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -e ".[research]"
  mkdir -p data/universe data/exports data/qlib reports
  echo "Daozang research dependencies installed. Enable RUN_DAOZANG_* refresh switches in beichen-alpha/config/local.env when ready." >&2
else
  echo "Skip Daozang research dependencies (set INSTALL_DAOZANG_RESEARCH=true to refresh Qlib/LightGBM scores on server)." >&2
fi

cd "$APP_DIR"
find "$APP_DIR" -type f -name '._*' -delete 2>/dev/null || true
for unit in deploy/systemd/beichen-alpha*.service deploy/systemd/beichen-alpha*.timer; do
  run_root cp "$unit" /etc/systemd/system/
done
run_root systemctl daemon-reload

enable_timer_if "$ENABLE_DAILY_TIMER" beichen-alpha.timer
enable_timer_if "$ENABLE_CHECK0945_TIMER" beichen-alpha-check0945.timer
enable_timer_if "$ENABLE_FOCUS0955_TIMER" beichen-alpha-focus0955.timer
enable_timer_if "$ENABLE_FOCUS1000_TIMER" beichen-alpha-focus1000.timer
enable_timer_if "$ENABLE_POSITION1030_TIMER" beichen-alpha-position1030.timer
enable_timer_if "$ENABLE_INTRADAY_MONITOR_TIMER" beichen-alpha-intraday-monitor.timer
enable_timer_if "$ENABLE_POOL1540_TIMER" beichen-alpha-pool1540.timer
enable_timer_if "$ENABLE_DATA_PREWARM_TIMER" beichen-alpha-data-prewarm.timer

if [ "$ENABLE_CHAT_SERVICE" = "true" ]; then
  run_root systemctl enable beichen-alpha-chat.service
  run_root systemctl restart beichen-alpha-chat.service
else
  echo "Skip enabling beichen-alpha-chat.service (set ENABLE_CHAT_SERVICE=true to enable)." >&2
fi

if [ "$RUN_DEPLOY_HEALTHCHECK" = "true" ]; then
  cd "$APP_DIR/beichen-alpha"
  ./scripts/server_healthcheck.sh
fi

echo "Deploy complete: $APP_DIR"
