# Quant AI Briefing PRD

## Overview

Quant AI Briefing is a personal investment monitoring and AI reporting system. It runs as a Python command-line application, stores shadow-accounting data and historical portfolio snapshots in SQLite, fetches public market quotes, calculates portfolio performance, generates Markdown daily or weekly reports with an OpenAI-compatible chat completion API, and optionally pushes them through WeCom.

The system is not an automated trading platform. It does not connect to brokerage trading APIs and does not execute real buy or sell orders.

## Goals

- Maintain configurable investment rules.
- Generate shadow trades from daily, weekly, or monthly rules.
- Prevent duplicate shadow trades for the same rule, asset, action, and date.
- Fetch public quotes with timeouts, retry, and graceful fallback.
- Calculate positions, cost, market value, daily PnL, floating PnL, and PnL percentage.
- Generate a human-readable Markdown daily briefing.
- Skip daily processing on weekends and Chinese statutory holidays.
- Generate a Sunday weekly briefing from persisted daily snapshots.
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

SQLite stores five tables:

- `investment_rules`: recurring shadow-investment rules.
- `trade_history`: generated or imported shadow trades.
- `market_snapshots`: cached quote snapshots by asset, market type, and date.
- `portfolio_snapshots`: daily portfolio totals by date and currency.
- `position_snapshots`: daily per-position cost, value, PnL, and quote metadata.

Database files contain personal financial data and must not be committed.

## Reporting Behavior

The command accepts `--report-kind daily|weekly`, defaulting to `daily`.

Daily mode:

- Runs only on Monday through Friday when the date is not a Chinese statutory holiday.
- Weekend makeup workdays remain excluded.
- Skips all database initialization, quote fetching, accounting, AI, and notification work when the date is not eligible.
- Runs shadow accounting, saves an idempotent daily snapshot, and generates the report on eligible dates.

The daily report should include:

- Account summary by currency.
- Position detail table with cost, market value, daily PnL, floating PnL, and PnL percentage.
- Contribution and drag analysis.
- Broader market watchlist performance.
- Disciplined review notes and risk reminders.

Weekly mode:

- Uses the completed Monday-to-Friday calendar window anchored on Sunday.
- Does not run shadow accounting or fetch live position quotes.
- Uses the latest saved daily snapshot in the week and the latest snapshot before Monday as its comparison baseline.
- Sends a short no-data report when no valid daily snapshot exists for the week.

The weekly report should include:

- Reporting window and ending portfolio state.
- Weekly market-value and floating-PnL changes when a baseline exists.
- Ending position details, top contributor, top detractor, and next-week observations.

The report must not provide deterministic buy/sell instructions or promise returns.
Live runs require a successful AI response. Local deterministic fallback reports are reserved for dry-run testing.

## Notification Behavior

WeCom Markdown messages have content size limits. Long reports must be split into multiple UTF-8-safe chunks and sent sequentially.

## Deployment

The expected deployment target is a Linux server with Python 3.10+ and cron. Weekday cron triggers daily mode at 08:00, while Sunday cron triggers weekly mode at 08:00. Both commands use the same `flock` lock file and append output to a local cron log. Daily mode applies the statutory-holiday check inside the application.

## Acceptance Criteria

- `python -m src.main --dry-run` completes without external credentials or push messages.
- `python -m src.main` generates a report when live AI configuration exists.
- `python -m src.main --send` sends a WeCom report when WeCom configuration exists.
- Ineligible daily dates exit successfully without accounting, external API calls, or notification attempts.
- Weekly mode reads saved snapshots without generating shadow trades.
- Re-running the same daily date upserts snapshots and does not duplicate shadow trades.
- Tests pass with `pytest -q`.
- No credentials, logs, or SQLite databases are committed to source control.
