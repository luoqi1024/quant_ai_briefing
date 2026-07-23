# Quant AI Briefing

[中文说明](README.zh-CN.md) | English

Quant AI Briefing is a small Python service for personal investment shadow accounting and AI briefings. It stores investment rules, shadow trades, market data, and daily portfolio snapshots in SQLite, calculates position cost and floating PnL, asks an OpenAI-compatible chat completion API to write Markdown daily or weekly reports, and can push them through a WeCom self-built app.

This project does not connect to brokerage trading APIs and does not place real orders. It is intended for personal review, record keeping, and portfolio monitoring.

## Features

- SQLite-backed investment rules, shadow trades, market snapshots, and historical portfolio snapshots.
- Daily, weekly, and monthly rule triggering.
- Duplicate protection for same-day generated shadow trades.
- Position cost, market value, daily PnL, floating PnL, and PnL percentage.
- Public quote sources for US equities, China funds/ETFs, gold, and selected market watchlist assets.
- Workday-only daily briefings using the Chinese statutory holiday calendar.
- Sunday weekly briefings based on persisted Monday-to-Friday snapshots.
- Separate AI prompts and local fallback templates for daily and weekly reports.
- WeCom Markdown push notifications with automatic message splitting under WeCom byte limits.
- `--dry-run` mode for local testing without external AI or push credentials.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

Copy the example environment file:

```bash
cp .env.example .env
```

Environment variables:

| Variable | Description |
| --- | --- |
| `DATABASE_PATH` | SQLite database path. Defaults to `quant_data.db`. |
| `LOG_LEVEL` | Python log level. Defaults to `INFO`. |
| `AI_API_KEY` | API key for an OpenAI-compatible chat completion endpoint. |
| `AI_URL` | Chat completion URL, for example `https://api.deepseek.com/chat/completions`. |
| `AI_MODEL` | Model name, for example `deepseek-v4-flash`. |
| `WECOM_CORPID` | WeCom corporation ID. |
| `WECOM_AGENTID` | WeCom self-built app agent ID. |
| `WECOM_SECRET` | WeCom self-built app secret. |

Legacy `XIAOMI_AI_API_KEY`, `XIAOMI_AI_URL`, and `XIAOMI_AI_MODEL` variables are still accepted for backward compatibility, but new deployments should use the generic `AI_*` names.

Do not commit `.env`, SQLite databases, logs, or personal portfolio data. They are ignored by default.

## Run

Dry run with mock quotes and no push:

```bash
python -m src.main --dry-run
```

Generate a real report but only print it:

```bash
python -m src.main
```

Generate and push through WeCom:

```bash
python -m src.main --report-kind daily --send
```

Generate a weekly summary from saved daily snapshots:

```bash
python -m src.main --report-kind weekly
```

Run for a specific date:

```bash
python -m src.main --date 2026-05-07 --dry-run
```

## Import Example Portfolio

The app initializes with two simple sample rules. For a personal portfolio, create your own rules and trades in SQLite, or adapt `scripts/import_portfolio_csv.py` with a CSV file that contains your own sanitized data.

Expected CSV columns:

```text
asset_name,asset_code,market_type,currency,rule_amount,freq_type,freq_value,enabled,trade_date,amount,price,shares
```

Daily mode exits successfully before fetching quotes or calling external APIs on weekends and Chinese statutory holidays. Weekend makeup workdays are still skipped by design. Weekly mode does not create shadow trades or fetch live position quotes; it reads the latest saved snapshots for the completed Monday-to-Friday window.

In live mode, AI configuration is required and an AI API failure stops the run. The deterministic local fallback is used by `--dry-run` for testing.

## Deployment Example

Example cron jobs for weekday daily reports and Sunday weekly reports at 08:00:

```cron
0 8 * * 1-5 cd /opt/quant-ai-briefing && /usr/bin/flock -n /tmp/quant-ai-briefing.lock /opt/quant-ai-briefing/.venv/bin/python -m src.main --report-kind daily --send >> /opt/quant-ai-briefing/cron.log 2>&1
0 8 * * 0 cd /opt/quant-ai-briefing && /usr/bin/flock -n /tmp/quant-ai-briefing.lock /opt/quant-ai-briefing/.venv/bin/python -m src.main --report-kind weekly --send >> /opt/quant-ai-briefing/cron.log 2>&1
```

Adjust paths for your server.

## Test

```bash
pytest -q
```

## Disclaimer

Generated reports are for personal review only and are not investment advice. Market data sources may be delayed, unavailable, or inaccurate. Always verify important numbers independently before making financial decisions.
