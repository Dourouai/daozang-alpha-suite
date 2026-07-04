# Daozang Alpha Suite

Daozang Alpha Suite is a local monorepo for an A-share short-term trading research assistant.

## Projects

- `beichen-alpha`: execution assistant, candidate screening, position review, Feishu notifications, and decision logs.
- `daozang-alpha`: Qlib research workspace, Alpha158 baseline experiments, model scores, and backtest reports.

## Boundaries

- Keep private runtime data local: Feishu webhooks, current positions, decision logs, downloaded market data, model artifacts, and virtual environments are ignored by Git.
- Commit source code, tests, scripts, config examples, docs, and small watchlists.
- Use `beichen-alpha` for trading workflow orchestration and `daozang-alpha` for model research. Model exports can be consumed locally by Beichen, but generated exports are not committed by default.

## Useful Checks

```bash
cd beichen-alpha
PYTHONPATH=src python3 -m unittest discover -s tests

cd ../daozang-alpha
PYTHONPATH=src python3 -m unittest discover -s tests
```
