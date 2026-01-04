# Configuration model

## Goals

- Make thresholds and behaviors editable without code changes.
- Support per-environment overrides (local vs prod).
- Make it obvious in the UI what configuration produced a given signal.

## Layers (precedence)

1. **Environment variables** (deployment secrets and absolute overrides)
2. **Database config** (runtime editable)
3. **Checked-in defaults** (safe baseline)

## Config surface area

### Connectivity

- `DATABASE_URL` (required)
- `HTTP_TIMEOUT_SECONDS` (default 10)
- `HTTP_MAX_CONCURRENCY` (default 10)
- `SCHEDULER_ENABLED` (default true)

### Ingestion schedule

- `SYNC_GAMMA_EVENTS_INTERVAL_SECONDS` (default 600)
- `SYNC_TRADES_INTERVAL_SECONDS` (default 45)
- `SYNC_OI_INTERVAL_SECONDS` (default 300)
- `SYNC_UNIVERSE_INTERVAL_SECONDS` (default 900)
- `ORDERBOOK_SNAPSHOT_INTERVAL_SECONDS` (default 300) (websocket heal)
- `GAMMA_EVENTS_PAGE_LIMIT` (default 100)
- `GAMMA_EVENTS_MAX_PAGES` (default 50)
- `TRADES_PAGE_LIMIT` (default 500)
- `TRADE_SAFETY_WINDOW_SECONDS` (default 300)

### Universe selection

- `MAX_TRACKED_MARKETS` (default 200)
- `MIN_GAMMA_VOLUME` (default 50000) (USD, heuristic)
- `MIN_GAMMA_LIQUIDITY` (default 10000) (USD, heuristic)
- `MIN_OPEN_INTEREST` (default 5000) (USD)

### Wallet/trade signals

- `TAKER_ONLY` (default true)
- `LARGE_TRADE_USD_THRESHOLD` (default 10000)
- `NEW_WALLET_WINDOW_DAYS` (default 14)
- `DORMANT_WINDOW_DAYS` (default 30)
- `TRACK_WALLET_DAYS_AFTER_LARGE_TRADE` (default 7)

### Arbitrage signals

- `ARB_EDGE_MIN` (default 0.01)
- `ARB_MIN_EXECUTABLE_SHARES` (default 50)
- `ARB_MAX_SHARES_TO_EVALUATE` (default 5000)
- `ARB_MAX_BOOK_AGE_SECONDS` (default 10)
- `ARB_MARKET_COOLDOWN_SECONDS` (default 60)
- `TAKER_FEE_BPS` (default 0)

### Alerting

- `ALERTS_ENABLED` (default false)
- `ALERT_CHANNELS` (comma-separated: `slack,email,telegram`)
- `ALERT_DEDUP_WINDOW_SECONDS` (default 600)
- `ALERT_MIN_SEVERITY` (default 2)
- `ALERT_SLACK_WEBHOOK_URL` (optional)
- `ALERT_TELEGRAM_BOT_TOKEN` (optional)
- `ALERT_TELEGRAM_CHAT_ID` (optional)

### Websocket

- `CLOB_WS_ENABLED` (default false)
- `CLOB_WS_PING_SECONDS` (default 10)

## Storage of config in DB

Table `app_config` (proposed):
- `key` (pk)
- `value` (jsonb)
- `updated_at`
- `updated_by` (nullable)

UI:
- “Config” page to edit `app_config`.
- Every `signal_events.payload` should include a `config_snapshot` sub-object with the keys relevant to that signal (not everything).

## Safety constraints

- Validate config ranges (e.g., `ARB_EDGE_MIN` must be between 0 and 0.05).
- If config is invalid, reject update and log.
