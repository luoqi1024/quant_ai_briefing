# Quant AI Briefing PRD

## Overview

Quant AI Briefing is a personal investment monitoring and AI reporting system. It runs as a Python command-line application, stores shadow-accounting data in SQLite, fetches public market quotes, calculates portfolio performance, generates a Markdown report with an OpenAI-compatible chat completion API, and optionally pushes the report through WeCom.

The system is not an automated trading platform. It does not connect to brokerage trading APIs and does not execute real buy or sell orders.

## Goals

- Maintain configurable investment rules.
- Generate shadow trades from daily, weekly, or monthly rules.
- Prevent duplicate shadow trades for the same rule, asset, action, and date.
- Fetch public quotes with timeouts, retry, and graceful fallback.
- Calculate positions, cost, market value, daily PnL, floating PnL, and PnL percentage.
- Generate a human-readable Markdown daily briefing.
- Push the briefing through WeCom Markdown messages.
- Support cron-based Linux server deployment.
- Provide a dry-run mode that does not require external credentials.

## Non-Goals

- No real trading.
- No brokerage integration.
- No multi-user permission system.
- No web frontend.
- No promise of real-time or authoritative market data.

## Runtime Configuration

Configuration is read from environment variables, normally via `.env`. The repository must only contain `.env.example`; real credentials must remain private.

Required variables for live AI report generation:

```text
XIAOMI_AI_API_KEY=
XIAOMI_AI_URL=
XIAOMI_AI_MODEL=
```

Required variables when `--send` is used:

```text
WECOM_CORPID=
WECOM_AGENTID=
WECOM_SECRET=
```

Common runtime variables:

```text
LOG_LEVEL=INFO
DATABASE_PATH=quant_data.db
```

## Data Model

SQLite stores three tables:

- `investment_rules`: recurring shadow-investment rules.
- `trade_history`: generated or imported shadow trades.
- `market_snapshots`: cached quote snapshots by asset, market type, and date.

Database files contain personal financial data and must not be committed.

## Reporting Behavior

The daily report should include:

- Account summary by currency.
- Position detail table with cost, market value, daily PnL, floating PnL, and PnL percentage.
- Contribution and drag analysis.
- Broader market watchlist performance.
- Disciplined review notes and risk reminders.

The report must not provide deterministic buy/sell instructions or promise returns.

## Notification Behavior

WeCom Markdown messages have content size limits. Long reports must be split into multiple UTF-8-safe chunks and sent sequentially.

## Deployment

The expected deployment target is a Linux server with Python 3.10+ and cron. A typical cron schedule runs `python -m src.main --send` once per day and writes logs to a local file.

## Acceptance Criteria

- `python -m src.main --dry-run` completes without external credentials or push messages.
- `python -m src.main` generates a report when live AI configuration exists.
- `python -m src.main --send` sends a WeCom report when WeCom configuration exists.
- Tests pass with `pytest -q`.
- No credentials, logs, or SQLite databases are committed to source control.
