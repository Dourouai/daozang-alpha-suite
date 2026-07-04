# Daozang Alpha Suite

Daozang Alpha Suite is a local monorepo for an A-share short-term trading research assistant.

## Projects

- `beichen-alpha`: execution assistant, candidate screening, position review, Feishu notifications, Feishu chat adapter, and decision logs.
- `daozang-alpha`: Qlib research workspace, Alpha158 baseline experiments, model scores, and backtest reports.

## Boundaries

- Keep private runtime data local: Feishu webhooks, current positions, decision logs, downloaded market data, model artifacts, and virtual environments are ignored by Git.
- Commit source code, tests, scripts, config examples, docs, and small watchlists.
- Use `beichen-alpha` for trading workflow orchestration and `daozang-alpha` for model research. Model exports can be consumed locally by Beichen, but generated exports are not committed by default.
- Keep Feishu as a single Beichen-owned channel. Daozang exports model scores and reports; Beichen owns notifications, chat replies, and later human-facing workflow orchestration.

## Useful Checks

```bash
cd beichen-alpha
PYTHONPATH=src python3 -m unittest discover -s tests

cd ../daozang-alpha
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Deployment

Server preparation notes live in [`docs/deployment.md`](docs/deployment.md).

The short version:

- clone this monorepo to a stable path such as `/opt/daozang-alpha-suite`
- create `beichen-alpha/config/local.env` from the example
- create local-only `beichen-alpha/data/positions/current_positions.json`
- run `beichen-alpha/scripts/server_healthcheck.sh`
- schedule `beichen-alpha/scripts/server_daily_run.sh` with systemd or cron
- optionally expose `beichen-alpha/scripts/beichen_chat_server.sh` for Feishu app event callbacks at `/feishu/events`
