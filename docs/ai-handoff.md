# Daozang Alpha Suite AI Handoff

Last updated: 2026-07-05

This document is the first file another AI agent should read before changing the project. The suite is a personal A-share short-term research assistant. It does not place orders and does not provide investment advice.

## Goal

Build a 2-3 trading day A-share research and decision assistant:

```text
Daozang Alpha finds statistical edge.
Beichen Alpha turns signals into executable research plans.
Feishu is the notification and conversation surface.
```

The user cares about:

- A-share short-term rotation, usually around 3 trading days.
- Current holdings, entry date, time stop, and capital rotation.
- Medical and innovation-drug themes as priority themes.
- Oil, energy, coal, resource/material names excluded from new holdings unless explicitly overridden.
- Global market linkage as context and model features, not as a standalone buy trigger.
- Every recommendation and trade plan should be logged for later review.

## Repository Map

```text
daozang-alpha-suite/
  README.md
  docs/
    ai-handoff.md              # this file
    project-structure.md       # high-level architecture map
    deployment.md              # server, Feishu, systemd, GitHub deploy notes
  scripts/
    server_daily_run.sh        # top-level wrapper; delegates to Beichen daily run
  deploy/
    cron/
    nginx/
    scripts/
    systemd/
  daozang-alpha/
    src/daozang_alpha/
      cli.py                   # doctor, baseline, score export, universe sync
      baseline.py              # Qlib Alpha158 + LightGBM baseline
      universe.py              # active 800 pool, industry map, risk calendar sync
      export_scores.py         # normalizes model score CSV for Beichen
      qlib_env.py              # Qlib environment checks
    data/                      # generated local data, ignored by Git
    reports/                   # generated research reports, ignored by Git
  beichen-alpha/
    src/beichen_alpha/
      cli.py                   # candidate, trade-plan, healthcheck, chat-server
      strategy/                # deterministic scoring and execution rules
      data_sources/            # AKShare, BaoStock, Tencent, FRED, yfinance, Qlib
      news_sources/            # stock news and opinion-signal events
      disclosure_sources/      # CNINFO disclosure risk events
      risk_sources/            # release, earnings, pledge, hard risk windows
      chat/                    # daocang Feishu event adapter and router
      notifiers/               # Beichen one-way Feishu webhook sender
      reports/                 # human-readable text/card renderers
      decision_log.py          # recommendation and trade-plan JSONL records
    scripts/
      server_daily_run.sh      # main scheduled production flow
      server_healthcheck.sh
      beichen_trade_plan_feishu.sh
      beichen_position_review_feishu.sh
      beichen_chat_server.sh
      feishu_send.sh
    data/                      # runtime data, mostly ignored by Git
```

## System Boundaries

### Daozang Alpha

Daozang is the offline research and model layer.

It owns:

- Qlib environment and CN data checks.
- Active universe construction from Beichen positions/watchlists/cache.
- AKShare/东方财富 industry and risk-calendar enrichment for the active universe.
- Alpha158 + LightGBM baseline.
- Future 5-day return label research.
- Exported model score files for Beichen.

It does not own:

- Feishu formatting.
- Intraday execution decisions.
- Position review.
- Final buy/sell plan wording.

Primary interface:

```text
daozang-alpha/data/exports/alpha_scores_latest.csv
```

### Beichen Alpha

Beichen is the execution-prep and decision assistant layer.

It owns:

- Candidate screening and watchlists.
- Market regime, market structure, sector rotation, macro and global linkage factors.
- Stock profile, theme, liquidity, news, disclosure, and risk-calendar factors.
- Reading Daozang model percent-rank as one factor.
- Position review, time stop, capital rotation, and 2-3 day trade plans.
- Decision logs.
- Beichen webhook push and daocang Feishu chat replies.

It does not own:

- Large-scale Qlib research experiments.
- Raw model training lifecycle.
- Automatic trading or order placement.

### Feishu

Feishu is an interface layer only.

- Beichen custom webhook: one-way cards and text notifications.
- `daocang` Feishu app bot: two-way chat through event callbacks.
- Daozang should not keep a separate webhook.

Do not put scoring logic inside Feishu adapters.

## Daily Data Flow

The scheduled production path is:

```text
beichen-alpha/scripts/server_daily_run.sh
  -> daozang sync-akshare-industry-map
  -> daozang sync-akshare-risk-calendar
  -> daozang sync-beichen-universe --limit 800
  -> daozang export-beichen-features
  -> daozang run-baseline --universe-file data/universe/active_universe.csv
  -> daozang export-scores
  -> beichen healthcheck
  -> beichen trade-plan
  -> optional Feishu notification
  -> decision logs
```

Server deployment is layered:

- P0 default: daocang Feishu chat service, Beichen daily research plan, one-way Feishu webhook, local decision logs.
- P1 optional: 09:45/10:30/focus checks, 10-minute intraday monitor, 15:40 closing pool refresh.
- P2 optional research: Daozang Qlib/LightGBM refresh on the server. This requires `INSTALL_DAOZANG_RESEARCH=true`; otherwise Beichen can still read the latest exported score CSV already present on disk and must mark stale/missing model scores clearly.

The deploy script is:

```bash
deploy/scripts/deploy_beichen_server.sh
```

Default deploy enables P0 only. Use `ENABLE_OPTIONAL_TIMERS=true` for all P1 timers, or enable specific timers such as `ENABLE_INTRADAY_MONITOR_TIMER=true`.

GitHub Actions deploys by cloning a clean temporary copy on the server and overlaying code into `/www/wwwroot/daozang-alpha-suite`; it sets `SKIP_GIT_UPDATE=true` when running the deploy script so a dirty server working tree does not block deploy. The overlay preserves local secrets, positions, decision logs, runtime cache, virtualenvs, and Daozang generated data.

The top-level wrapper is:

```bash
./scripts/server_daily_run.sh
```

The Beichen daily script is the real orchestrator:

```bash
cd beichen-alpha
./scripts/server_daily_run.sh
```

Important environment switches in `beichen-alpha/config/local.env`:

```bash
RUN_HEALTHCHECK=true
RUN_DAOZANG_SYNC_INDUSTRY_MAP=false
RUN_DAOZANG_SYNC_RISK_CALENDAR=false
RUN_DAOZANG_SYNC_UNIVERSE=false
RUN_DAOZANG_SYNC_QLIB_BARS=false
RUN_DAOZANG_EXPORT_BEICHEN_FEATURES=true
RUN_DAOZANG_BASELINE=auto
RUN_DAOZANG_EXPORT_SCORES=false
DAOZANG_BEICHEN_FEATURES_PATH="data/features/beichen_daily_features_latest.csv"
RUN_POOL_REFRESH=false
RUN_TRADE_PLAN=true
RUN_FOCUS_CHECK=false
DAOZANG_UNIVERSE_LIMIT=800
BEICHEN_CAPITAL=10000
BEICHEN_TRADE_TOP=3
BEICHEN_BROAD_WATCHLIST="data/watchlists/broad_target_pool_latest.txt"
BEICHEN_EXCLUDE_TRADE_GROUPS="能源,石油石化,煤炭,资源,材料资源"
BEICHEN_PREFER_TRADE_GROUPS="医药,创新药"
```

The values above are the P0 server-safe defaults. For P2 server-side Daozang model refresh, install Daozang research dependencies and switch the Daozang refresh variables to `true`.

Never commit `config/local.env`.

## Data Contracts

### Daozang score CSV

Path:

```text
daozang-alpha/data/exports/alpha_scores_latest.csv
```

Required columns:

```text
trade_date,instrument,score,rank,pct_rank,model,feature_set,horizon_days,universe
```

Optional multi-horizon columns:

```text
score_1d,score_3d,score_5d,pct_rank_1d,pct_rank_3d,pct_rank_5d,expected_return_3d,up_probability_3d
```

Rules:

- `instrument` should be Qlib style, such as `SH600036` or `SZ002415`.
- `pct_rank` is `0.0-1.0`; higher is better. In the multi-horizon export it is a compatibility alias for `pct_rank_3d`.
- `expected_return_3d` and `up_probability_3d` are validation-bucket calibrations, not guaranteed returns.
- Beichen treats this as a model factor only. It never bypasses hard risk filters or execution checks.
- If the file is missing, stale, or does not cover a candidate, Beichen must say "模型未覆盖" or equivalent.

### Beichen daily feature CSV

Path:

```text
daozang-alpha/data/features/beichen_daily_features_latest.csv
```

Generated by:

```bash
PYTHONPATH=src .venv/bin/python -m daozang_alpha export-beichen-features \
  --beichen-root ../beichen-alpha
```

Purpose:

```text
Beichen structured decision factors -> daily numeric features -> Daozang model training
```

Current feature families:

- `beichen_policy_*`
- `beichen_flow_*`
- `beichen_disclosure_*`
- `beichen_sector_lifecycle_*`
- `beichen_expectation_*`

Daozang consumes it with:

```bash
PYTHONPATH=src .venv/bin/python -m daozang_alpha run-baseline \
  --extra-features data/features/beichen_daily_features_latest.csv
```

### Active universe CSV

Path:

```text
daozang-alpha/data/universe/active_universe.csv
```

Typical important columns:

```text
code,instrument,name,source_pool,industry,industry_source,themes,latest,turnover,
market_cap_billion,data_start_date,data_end_date,history_days,amount_5d_avg,
amount_20d_avg,volatility_20d,risk_tags,risk_source,risk_detail,is_priority,is_excluded
```

Current target size is 800 names. Keep industry/theme coverage high enough for Beichen explanations.

### Positions JSON

Path:

```text
beichen-alpha/data/positions/current_positions.json
```

This file is private runtime state and ignored by Git.

Schema:

```json
{
  "account": "optional account label",
  "positions": [
    {
      "code": "600036",
      "name": "招商银行",
      "shares": 100,
      "entry_date": "2026-07-03",
      "cost": 36.89,
      "confirm": 36.80,
      "invalid": 35.28,
      "target": 39.23
    }
  ]
}
```

`entry_date` is required for real day-2/day-3 time-stop logic.

### Decision logs

Path:

```text
beichen-alpha/data/decision_logs/recommendations.jsonl
```

This is private runtime state and ignored by Git. Every recommendation, trade plan, and ad hoc stock review should append a structured record when it is generated through Beichen CLI or chat flows.

Use the log later to backfill:

- Whether the plan triggered.
- 1-day, 3-day, and 5-day forward returns.
- Whether stop/target was hit.
- Which factor was useful or misleading.

## Main Commands

### Health checks

```bash
cd daozang-alpha
PYTHONPATH=src .venv/bin/python -m daozang_alpha doctor
```

```bash
cd beichen-alpha
./scripts/server_healthcheck.sh
```

### Refresh Daozang 800-pool scores

```bash
cd daozang-alpha
PYTHONPATH=src .venv/bin/python -m daozang_alpha sync-akshare-industry-map \
  --target-universe data/universe/active_universe.csv
PYTHONPATH=src .venv/bin/python -m daozang_alpha sync-akshare-risk-calendar \
  --target-universe data/universe/active_universe.csv
PYTHONPATH=src .venv/bin/python -m daozang_alpha sync-beichen-universe \
  --beichen-root ../beichen-alpha \
  --limit 800
PYTHONPATH=src .venv/bin/python -m daozang_alpha run-baseline \
  --quick \
  --universe-file data/universe/active_universe.csv \
  --max-instruments 800 \
  --top-n 800
PYTHONPATH=src .venv/bin/python -m daozang_alpha export-scores
```

### Generate a 2-3 day Beichen plan

```bash
cd beichen-alpha
PYTHONPATH=src python3 -m beichen_alpha trade-plan \
  --source akshare \
  --positions data/positions/current_positions.json \
  --watchlist data/watchlists/broad_target_pool_latest.txt \
  --priority-watchlist data/watchlists/innovation_drug_pool.txt \
  --model-scores ../daozang-alpha/data/exports/alpha_scores_latest.csv \
  --capital 10000 \
  --top 3 \
  --review-date 20260706 \
  --exclude-trade-groups "能源,石油石化,煤炭,资源,材料资源" \
  --prefer-trade-groups "医药,创新药" \
  --decision-log data/decision_logs/recommendations.jsonl
```

### Feishu push

```bash
cd beichen-alpha
./scripts/feishu_send.sh "message text"
```

`feishu_send.sh` needs `FEISHU_WEBHOOK` in `config/local.env`.

### daocang Feishu chat

```bash
cd beichen-alpha
./scripts/beichen_chat_server.sh
```

Runtime endpoints:

```text
GET /health
POST /feishu/events
```

## Test Commands

Run before pushing code changes when feasible:

```bash
cd beichen-alpha
PYTHONPATH=src python3 -m unittest discover -s tests
```

```bash
cd daozang-alpha
PYTHONPATH=src python3 -m unittest discover -s tests
```

For documentation-only edits, tests are usually not required. Say clearly if they were not run.

## Current Verified State

As of 2026-07-05:

- Server deployment target is documented in `docs/deployment.md`.
- Server path is expected to be `/www/wwwroot/daozang-alpha-suite`.
- `daozang-alpha` server venv has Qlib, LightGBM, and AKShare installed.
- Active universe target is 800 names.
- Latest known Daozang score date after server run: 2026-07-03.
- Latest score export has 799 data rows plus header because one active-universe stock lacked fresh Qlib data.
- Beichen can read Daozang scores and show coverage in trade plans.
- Beichen trade plans support entry-date based holding-day logic and rotation budget.
- Beichen and Daozang are linked by `alpha_scores_latest.csv`; the bridge exists, but data freshness and coverage checks must stay visible.

If local files disagree with server output, check which environment ran last. Do not assume local generated data is current.

## Decision Stack

For any stock or plan, reason in this order:

1. Data freshness and source health.
2. Hard exclusions: ST, delisting risk, new stock risk, major disclosure risk, hard unlock/risk calendar events.
3. User constraints: short-term 2-3 day horizon, no new oil/energy/resource exposure by default, medical/innovation-drug preference.
4. Market regime, market structure, and sector rotation.
5. Stock profile: industry, themes, liquidity, market cap, volatility.
6. News, policy, official disclosures, and manually ingested opinion signals.
7. Daozang model percent-rank.
8. Price structure: observation zone, confirm price, chase limit, stop, target.
9. Existing holdings: entry date, PnL, day-2/day-3 time stop, capital efficiency.
10. Decision log entry and Feishu/user-facing explanation.

Never turn a model score into a direct buy command. Candidate means "watch or conditionally execute", not "buy now".

## Feishu Wording Rules

Keep Feishu messages short, concrete, and executable:

- Include date and horizon.
- Include status: observe, conditional buy, reduce, exit priority, continue holding.
- Include confirm price, chase limit, stop, and target.
- Say "模型未覆盖" when Daozang score is missing.
- Say "仅用于个人研究和策略测试，不构成投资建议。"
- Avoid unexplained words like "失效"; use "风控线" or "计划失效线" for humans.

## Common Pitfalls

- Free data sources fail often. Treat source failures as data-health events, not trading signals.
- Qlib fields contain `$`; when using remote shell one-liners, avoid shell expansion. Use Python `chr(36)` or quote carefully.
- Local and server generated data can diverge. Verify `trade_date`, row counts, and coverage before using model scores.
- Feishu custom webhook cannot receive messages. Two-way chat requires the `daocang` app event callback.
- Do not commit local env files, webhook URLs, app secrets, current positions, logs, runtime cache, Qlib datasets, or model artifacts.
- Do not revert unrelated dirty files. The repo often has in-progress changes from previous sessions.
- A-share T+1 matters: existing holdings can be sold/reduced next trading day; same-day new buys cannot be sold until the next trading day.

## Best Next Work

Recommended priorities:

1. Intraday review scheduler for `09:40`, `10:30`, and `14:30`.
2. Outcome backfill for each decision log: trigger, 1D/3D/5D return, stop/target hit.
3. Historical probability calibration so "上涨概率" is based on real samples instead of a fixed placeholder.
4. Stronger active-universe layers: core 100, active 300, full 800.
5. More complete theme tags for innovation drug, humanoid robot, AI hardware, auto parts, semiconductor, non-bank financial, dividend, utilities.
6. Data-health Feishu card before any daily plan.
7. Better chat commands: `复核 601689`, `推荐 医疗 3只`, `10:30复核`, `为什么不买`.
8. Server CI/deploy hardening and clearer logs for scheduled jobs.

## Safe Handoff Checklist

When another AI takes over:

1. Read this file, `docs/project-structure.md`, and `docs/deployment.md`.
2. Run `git status --short` and do not revert unrelated changes.
3. Check whether the task is local docs/code, server runtime, or Feishu interaction.
4. If making market recommendations, verify data freshness and latest news/source state first.
5. If changing code, run the relevant unit tests when feasible.
6. If sending Feishu messages, use Beichen's notifier scripts and do not paste secrets into tracked files.
7. If generating a recommendation or plan, ensure it is logged.
8. Summarize what changed and what was verified.

## Optimization Sprint (2026-07-05)

See [`CHANGELOG.md`](../CHANGELOG.md) for full details. Summary of changes:

### New Commands

```bash
# Backfill forward-return outcomes for past decisions
cd beichen-alpha
PYTHONPATH=src python3 -m beichen_alpha backfill-outcomes
PYTHONPATH=src python3 -m beichen_alpha backfill-outcomes --horizons 1,3,5

# Check data health before running trade plans
PYTHONPATH=src python3 -m beichen_alpha data-health
PYTHONPATH=src python3 -m beichen_alpha data-health --notify feishu
```

### What Changed

| Module | Change |
|--------|--------|
| `outcome_backfill.py` | New. Reads decision logs, queries subsequent bars via Baostock, computes 1D/3D/5D/10D forward returns, checks stop/target hits. |
| `data_health.py` | New. Validates model score freshness, universe coverage, risk calendar age, positions, and Feishu webhook config. |
| `models.py` | Added `calibration_up_prob`, `calibration_avg_return`, `calibration_confidence` fields to `Recommendation`. |
| `strategy/engine.py` | Now calls `calibrate_position_return()` inside `build_recommendation()`. Every recommendation carries historically-calibrated win probability. |
| `strategy/trade_plan.py` | Score-weighted position sizing via `apply_score_weighted_sizing()`. Higher-conviction candidates get proportionally more capital. Max 30% single-position cap. |
| `cli.py` | Registered `backfill-outcomes` and `data-health` subcommands. |

### Recommended Daily Flow (Updated)

```bash
# 1. Check data health first
cd beichen-alpha
PYTHONPATH=src python3 -m beichen_alpha data-health

# 2. Generate trade plan (now includes calibrated win probabilities + score-weighted sizing)
PYTHONPATH=src python3 -m beichen_alpha trade-plan ...

# 3. Weekly: backfill outcomes to close feedback loop
PYTHONPATH=src python3 -m beichen_alpha backfill-outcomes
```

### Key Design Rules Added

- Outcome backfill uses **Baostock** (free, stable) to avoid AKShare rate limits.
- Calibration probabilities use **Laplace smoothing** `(successes+1)/(total+2)` to avoid 0%/100% extremes.
- Position sizing is **score-weighted**, not full Kelly — pragmatic until edge estimation is reliable.
- Calibration is **advisory only** — low confidence doesn't block recommendations, it's displayed as metadata.
