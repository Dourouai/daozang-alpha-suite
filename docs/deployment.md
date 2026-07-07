# Deployment Preparation

This repository is not an order-execution system. Server deployment is for scheduled research runs, Feishu notifications, local decision logs, and model feature refreshes.

## Recommended Server Shape

- Linux VM close to mainland China network routes, if possible.
- Python 3.11+.
- Enough disk for local market data and Qlib artifacts. Keep generated data outside Git.
- Server timezone set to `Asia/Shanghai`.

## Current Target

- Server IP: `43.155.159.149`
- Domain: `daozang.zaps.work`
- Visible Feishu app/bot name: `daocang`
- Feishu event callback URL: `https://daozang.zaps.work/feishu/events`

Create an A record for `daozang.zaps.work` pointing to `43.155.159.149`, then enable HTTPS in Baota before configuring the Feishu event callback.

## Deployable Components

The suite should be deployed in layers. This keeps the server useful while avoiding accidental noisy alerts or expensive model refreshes.

| Layer | Component | Status | Server unit / script | Notes |
| --- | --- | --- | --- | --- |
| P0 | daocang Feishu chat callback | Deploy now | `beichen-alpha-chat.service` / `scripts/beichen_chat_server.sh` | Required for two-way Feishu app chat. |
| P0 | Beichen daily research plan | Deploy now | `beichen-alpha.timer` / `scripts/server_daily_run.sh` | Runs data health, trade plan, model score checks, decision log, optional Feishu push. |
| P0 | Beichen one-way Feishu webhook | Deploy now | Called by Beichen scripts | Requires `FEISHU_WEBHOOK`; used for trade-plan and substantive alerts. |
| P0 | Local decision logs and runtime cache | Deploy now | `beichen-alpha/data/decision_logs`, `data/runtime` | Keep private and out of Git. |
| P1 | 09:45 / 10:30 / focus checks | Optional | `beichen-alpha-check0945.timer`, `beichen-alpha-position1030.timer`, focus timers | Enable after P0 is stable. |
| P1 | Intraday 10-minute monitor | Optional | `beichen-alpha-intraday-monitor.timer` / `scripts/intraday_5min_monitor.sh --once` | Name is historical; timer cadence is 10 minutes. It logs every run and sends Feishu only on substantive changes by default. |
| P1 | 15:40 closing pool refresh | Optional | `beichen-alpha-pool1540.timer` | Useful after the pool rules stabilize. |
| P2 | Daozang Qlib/LightGBM server refresh | Optional research | `scripts/server_daily_run.sh` Daozang steps | Requires `INSTALL_DAOZANG_RESEARCH=true`, more disk, and slower first run. |
| Not yet | Broker/order execution | Do not deploy | none | This project does not place orders. |
| Not yet | Paid data sources | Disabled by default | Tushare adapter | Only enable with explicit token and data checks. |

Recommended current server default:

```text
Deploy P0 now: chat callback + daily research plan + logs + Feishu webhook.
Keep P1 optional timers off until P0 has run cleanly for a few days.
Install P2 Daozang research deps only when the server is ready to refresh Qlib/LightGBM scores itself.
```

## Runtime Choice

Default for Phase 1: do not use Docker.

Use:

- Python virtualenv for Beichen runtime dependencies.
- systemd for the long-running daocang chat adapter.
- systemd timer for daily research runs.
- Baota/Nginx for HTTPS and reverse proxy.

This keeps the first deployment easy to inspect on the Baota server. Docker Compose can be added later if Qlib/LightGBM dependencies become hard to reproduce, or if the service needs to move between servers.

## Clone

```bash
git clone https://github.com/Dourouai/daozang-alpha-suite.git /www/wwwroot/daozang-alpha-suite
cd /www/wwwroot/daozang-alpha-suite
```

## Beichen Alpha Runtime

```bash
cd /www/wwwroot/daozang-alpha-suite/beichen-alpha
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e .
python3 -m pip install akshare baostock pandas yfinance beautifulsoup4
cp config/local.env.example config/local.env
```

Edit `config/local.env`:

```bash
export FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export FEISHU_SECRET=""
export FEISHU_APP_ID=""
export FEISHU_APP_SECRET=""
export FEISHU_EVENT_VERIFY_TOKEN=""
export FEISHU_ENCRYPT_KEY=""
export FEISHU_CHAT_HOST="127.0.0.1"
export FEISHU_CHAT_PORT="8787"
export FEISHU_CHAT_ALLOW_WEBHOOK_FALLBACK="false"
export RUN_HEALTHCHECK="true"
export RUN_DAOZANG_SYNC_INDUSTRY_MAP="false"
export RUN_DAOZANG_SYNC_RISK_CALENDAR="false"
export RUN_DAOZANG_SYNC_UNIVERSE="false"
export RUN_DAOZANG_SYNC_QLIB_BARS="false"
export RUN_DAOZANG_BASELINE="auto"
export RUN_DAOZANG_EXPORT_SCORES="false"
export RUN_POOL_REFRESH="false"
export RUN_TRADE_PLAN="true"
export RUN_FOCUS_CHECK="false"
export BEICHEN_CAPITAL="10000"
export BEICHEN_TRADE_TOP="3"
export BEICHEN_BROAD_WATCHLIST="data/watchlists/broad_target_pool_latest.txt"
export BEICHEN_EXCLUDE_TRADE_GROUPS="能源,石油石化,煤炭,资源,材料资源"
export BEICHEN_PREFER_TRADE_GROUPS="医药,创新药"
```

For P2 Daozang server-side model refresh, set the Daozang switches to `true` after running:

```bash
INSTALL_DAOZANG_RESEARCH=true bash deploy/scripts/deploy_beichen_server.sh
```

Create local-only runtime inputs:

```bash
mkdir -p data/positions data/decision_logs data/runtime logs
cp data/positions/current_positions.example.json data/positions/current_positions.json
```

If no example file exists yet, create `data/positions/current_positions.json` manually with:

```json
{
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

Run healthcheck:

```bash
./scripts/server_healthcheck.sh
```

Run one scheduled cycle:

```bash
./scripts/server_daily_run.sh
```

## Feishu Chat Adapter

Feishu has two roles:

- Beichen custom webhook: one-way cards and alerts.
- `daocang` Feishu app bot: two-way group chat through event subscription and message reply API.

Daozang does not keep a separate Feishu webhook; it exports model artifacts for Beichen and daocang to consume.

Custom Feishu webhooks are one-way. For true chat, create a `daocang` Feishu app, enable event subscription plus message reply permissions, add the app bot to the Feishu group, and expose this endpoint:

```bash
cd /www/wwwroot/daozang-alpha-suite/beichen-alpha
./scripts/beichen_chat_server.sh
```

Runtime endpoints:

- `GET /health`
- `POST /feishu/events`

If the Feishu app encryption strategy is enabled, copy both values from Feishu Open Platform into server-local `beichen-alpha/config/local.env`:

```bash
export FEISHU_EVENT_VERIFY_TOKEN="..."
export FEISHU_ENCRYPT_KEY="..."
```

Optional LLM fallback for natural-language chat:

```bash
export BEICHEN_CHAT_LLM_ENABLED="true"
export BEICHEN_LLM_API_KEY="..."
export BEICHEN_LLM_MODEL="gpt-4.1-mini"
export BEICHEN_LLM_BASE_URL="https://api.openai.com/v1"
```

When this is disabled or the key is missing, daocang still supports deterministic commands such as `持仓`, `计划`, `日志`, and `推荐3支股票`, but open-ended questions will return a configuration prompt.

In production, place this behind HTTPS, then configure the Feishu app event callback URL as:

```text
https://daozang.zaps.work/feishu/events
```

## Baota Reverse Proxy

In Baota, create a site for `daozang.zaps.work`, enable SSL, then add the Nginx location snippet from:

```text
deploy/nginx/daozang.zaps.work.locations.conf
```

After DNS and SSL are ready, test:

```bash
curl -fsS https://daozang.zaps.work/health
```

Expected response:

```json
{"ok": true}
```

## GitHub Auto Deploy

The workflow lives at:

```text
.github/workflows/deploy.yml
```

Add these GitHub repository secrets:

- `DAOZANG_DEPLOY_HOST`: `43.155.159.149`
- `DAOZANG_DEPLOY_USER`: SSH user, usually `root` on a Baota server
- `DAOZANG_DEPLOY_PORT`: SSH port, usually `22`
- `DAOZANG_DEPLOY_SSH_KEY`: private key allowed to SSH into the server

Optional GitHub repository variables:

- `DAOZANG_INSTALL_RESEARCH`: set to `true` to install Daozang Qlib/LightGBM research dependencies on the server.
- `DAOZANG_ENABLE_OPTIONAL_TIMERS`: set to `true` to enable all P1 optional systemd timers during deploy.

On each push to `main`, GitHub Actions will SSH into the server, clone a clean copy into a temporary directory, overlay code into `/www/wwwroot/daozang-alpha-suite`, install Beichen dependencies, copy systemd units, enable the P0 timers/services, and restart the `beichen-alpha-chat` service. Optional P1/P2 layers are controlled by the variables above.

The auto-deploy overlay intentionally preserves local runtime state:

- `beichen-alpha/config/local.env`
- `beichen-alpha/.venv`
- `beichen-alpha/data/positions/current_positions.json`
- `beichen-alpha/data/decision_logs/`
- `beichen-alpha/data/runtime/`
- `beichen-alpha/logs/`
- `daozang-alpha/.venv`
- `daozang-alpha/data/`
- `daozang-alpha/reports/`

This is deliberate because the server working tree may contain runtime files and local generated data. Code should be changed through Git; holdings, logs, secrets, and model artifacts remain server-local.

## Scheduling

P0 systemd example:

```bash
cd /www/wwwroot/daozang-alpha-suite
bash deploy/scripts/deploy_beichen_server.sh
systemctl list-timers 'beichen-alpha*'
```

Enable all optional P1 timers during a manual deploy:

```bash
cd /www/wwwroot/daozang-alpha-suite
ENABLE_OPTIONAL_TIMERS=true bash deploy/scripts/deploy_beichen_server.sh
```

Install Daozang research dependencies for server-side Qlib/LightGBM refresh:

```bash
cd /www/wwwroot/daozang-alpha-suite
INSTALL_DAOZANG_RESEARCH=true bash deploy/scripts/deploy_beichen_server.sh
```

Enable only the intraday 10-minute monitor:

```bash
cd /www/wwwroot/daozang-alpha-suite
ENABLE_INTRADAY_MONITOR_TIMER=true bash deploy/scripts/deploy_beichen_server.sh
```

The deploy script does not disable timers that were already enabled manually. To stop one:

```bash
sudo systemctl disable --now beichen-alpha-intraday-monitor.timer
```

Cron example:

```bash
crontab -e
```

Then adapt the line from:

```text
deploy/cron/beichen-alpha.cron.example
```

## Local-Only Files

These are intentionally ignored by Git:

- `beichen-alpha/config/local.env`
- `beichen-alpha/data/positions/current_positions.json`
- `beichen-alpha/data/decision_logs/`
- `beichen-alpha/data/runtime/`
- `beichen-alpha/logs/`
- `daozang-alpha/data/`
- `daozang-alpha/reports/`
- virtual environments

## Notes

- BaoStock, Tencent, AKShare, yfinance, and FRED can fail due network conditions. Treat failed runs as data-health events, not trading signals.
- Keep Feishu notifications as research alerts only.
- Keep Feishu replies as assistant output only; they are not order instructions.
- Keep decision logs private; they contain personal holdings and decision context.
