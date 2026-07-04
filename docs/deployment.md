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
git clone https://github.com/Dourouai/daozang-alpha-suite.git /opt/daozang-alpha-suite
cd /opt/daozang-alpha-suite
```

## Beichen Alpha Runtime

```bash
cd /opt/daozang-alpha-suite/beichen-alpha
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
export RUN_POOL_REFRESH="false"
export RUN_TRADE_PLAN="true"
export RUN_FOCUS_CHECK="false"
export BEICHEN_CAPITAL="10000"
export BEICHEN_TRADE_TOP="3"
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
cd /opt/daozang-alpha-suite/beichen-alpha
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

On each push to `main`, GitHub Actions will SSH into the server, update `/opt/daozang-alpha-suite`, install Beichen dependencies, install systemd units, and restart the `beichen-alpha-chat` service.

## Scheduling

Systemd example:

```bash
sudo cp /opt/daozang-alpha-suite/deploy/systemd/beichen-alpha.service /etc/systemd/system/
sudo cp /opt/daozang-alpha-suite/deploy/systemd/beichen-alpha.timer /etc/systemd/system/
sudo cp /opt/daozang-alpha-suite/deploy/systemd/beichen-alpha-chat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now beichen-alpha.timer
sudo systemctl enable --now beichen-alpha-chat.service
systemctl list-timers beichen-alpha.timer
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
