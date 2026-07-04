#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/daozang-alpha-suite}"
BRANCH="${BRANCH:-main}"
REPO_URL="${REPO_URL:-https://github.com/Dourouai/daozang-alpha-suite.git}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_DAOZANG_RESEARCH="${INSTALL_DAOZANG_RESEARCH:-false}"
RUN_DEPLOY_HEALTHCHECK="${RUN_DEPLOY_HEALTHCHECK:-false}"

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

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

cd "$APP_DIR/beichen-alpha"
if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[data,content]"

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
  if [ ! -x ".venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -e ".[research]"
fi

cd "$APP_DIR"
run_root cp deploy/systemd/beichen-alpha.service /etc/systemd/system/
run_root cp deploy/systemd/beichen-alpha.timer /etc/systemd/system/
run_root cp deploy/systemd/beichen-alpha-chat.service /etc/systemd/system/
run_root systemctl daemon-reload
run_root systemctl enable --now beichen-alpha.timer
run_root systemctl enable beichen-alpha-chat.service
run_root systemctl restart beichen-alpha-chat.service

if [ "$RUN_DEPLOY_HEALTHCHECK" = "true" ]; then
  cd "$APP_DIR/beichen-alpha"
  ./scripts/server_healthcheck.sh
fi

echo "Deploy complete: $APP_DIR"
