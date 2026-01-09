# polymercado

Python-first Polymarket research dashboard: ingest public Polymarket APIs, store locally (SQLite/Postgres), compute signals (wallet activity + arb), and serve a FastAPI web UI.

## What it does

- **Markets explorer**: filter/rank by tags, volume/liquidity, open interest, spreads.
- **Signals**: large taker trades, new/dormant wallet activity, binary arb (“buy YES + buy NO”).
- **Alerts**: log/Telegram/Slack, with dedupe + rules + acknowledgements.
- **Config**: defaults + DB-backed overrides via `/config` + env var overrides.

This project is intentionally **read-only**: no order placement / private endpoints.

## Quickstart

Prereqs: **Python 3.11+** and **uv**.

```bash
uv venv
uv sync --extra dev
cp .env.example .env  # optional (alerts, overrides)
uv run python -m polymercado
```

Open `http://127.0.0.1:8000`.

Development server with reload:

```bash
uv run uvicorn polymercado.web.app:app --reload
```

## Configuration

Settings live in `src/polymercado/config.py` (`AppSettings`).

Precedence:
1) defaults
2) DB overrides (`app_config` table; edit via `/config`)
3) environment variables (highest priority)

Common env vars:
- `HOST` / `PORT`: Uvicorn bind settings (used by `python -m polymercado`).
- `DATABASE_URL`: defaults to `sqlite:///./polymercado.db`. Postgres example: `postgresql+psycopg://user:pass@localhost:5432/polymercado`.
- `SCHEDULER_ENABLED`: run background ingestion/signal/alert jobs (default: `true`).
- `CLOB_WS_ENABLED`: run orderbook websocket client (default: `true`).
- `ALERTS_ENABLED`: enable alert delivery (default: `true`).
- `ALERT_CHANNELS`: comma-separated, e.g. `log`, `telegram`, `slack`.
- `ALERT_TELEGRAM_BOT_TOKEN` / `ALERT_TELEGRAM_CHAT_ID`: Telegram alert delivery.
- `ALERT_SLACK_WEBHOOK_URL`: Slack alert delivery.

For local dev, a `.env` file in the repo root is loaded automatically (see `.env.example`).

If you want a “UI only” mode (no network calls), start with:

```bash
SCHEDULER_ENABLED=false CLOB_WS_ENABLED=false uv run python -m polymercado
```

## Endpoints

- UI: `/markets`, `/wallets`, `/alerts`, `/config`, `/status`
- Probes: `/healthz`, `/readyz`

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Update API fixtures used by parser tests:

```bash
uv run python scripts/fetch_fixtures.py
```

## Docs

Implementation-ready product/architecture specs live in `specs/` (start at `specs/README.md`).
