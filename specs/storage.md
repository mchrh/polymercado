# Storage (Postgres) — schema & retention

## Goals

- Normalize core entities for joins and drilldowns.
- Preserve enough raw data to explain signals without ballooning storage.
- Enable efficient time-window queries (e.g., last 24h trades, last 30m arbs).

## Conventions

- Timestamps stored as `timestamptz` in UTC.
- IDs:
  - `condition_id` = 0x-prefixed 64-hex string (market identifier)
  - `token_id` = large numeric string (CLOB “asset_id”)
  - `wallet_address` = 0x-prefixed 40-hex string
- Prices stored as numeric (e.g., `numeric(18,8)`), sizes as numeric.

## Tables

### `markets`

Represents a Polymarket “market” (binary outcome market) keyed by `condition_id`.

Columns (minimum):
- `condition_id` (pk)
- `market_id` (Gamma id, nullable)
- `event_id` (Gamma event id, nullable)
- `slug`, `question`, `title`
- `tag_ids` (int[], denormalized for quick filters) + optional join table later
- `neg_risk` (bool)
- `outcomes` (jsonb) — store parsed outcomes list
- `token_ids` (jsonb) — `[yes_token_id, no_token_id]`
- `start_time`, `end_time` (nullable)
- `created_at`, `updated_at` (platform timestamps when known)
- `last_seen_at` (when we last refreshed this row)

Indexes:
- `slug` unique if available
- `event_id`, `tag_ids` GIN

### `market_metrics_ts`

Time-series snapshots for market-level metrics used in ranking.

Columns:
- `id` (pk)
- `condition_id` (fk markets)
- `ts` (snapshot time)
- `gamma_volume`, `gamma_liquidity` (numeric, nullable)
- `open_interest` (numeric, nullable)
- `best_bid_yes`, `best_ask_yes`, `best_bid_no`, `best_ask_no` (numeric, nullable)
- `spread_yes`, `spread_no` (numeric, nullable)

Indexes:
- `(condition_id, ts desc)`
- retention policy (see below)

### `orderbook_levels`

Latest aggregated levels per token (for arb computation).

Design choice:
- Store **latest** book in DB for audit + UI.
- Keep high-frequency updates in memory; flush periodically.

Columns:
- `token_id` (pk)
- `condition_id`
- `side` enum `BID|ASK`
- `levels` jsonb: array of `{price, size}` sorted best→worst
- `tick_size`, `min_order_size`, `neg_risk`
- `as_of` timestamptz
- `hash` (string, nullable)

Indexes:
- `condition_id`

### `trades`

Normalized trade prints from Data API `/trades`.

Columns:
- `trade_pk` (pk, synthetic)
- `transaction_hash` (unique when present)
- `wallet` (canonical: proxyWallet)
- `condition_id`, `token_id`
- `side` (`BUY|SELL`)
- `price`, `size` (shares), `notional_usd` (computed = `price * size`)
- `trade_ts` (from API timestamp)
- `raw` jsonb (optional, store minimal original for audit)

Indexes:
- `(trade_ts desc)`
- `(wallet, trade_ts desc)`
- `(condition_id, trade_ts desc)`
- `notional_usd` (btree) for threshold queries

### `wallets`

Wallet registry with first-seen and aggregates.

Columns:
- `wallet` (pk)
- `first_seen_at`
- `last_seen_at`
- `first_trade_ts` (nullable)
- `lifetime_notional_usd` (numeric)
- `last_7d_notional_usd` (numeric, derived)
- `notes` (text, optional manual annotation)

### `wallet_market_exposure`

Optional denormalized table for quick “wallet exposure” views.

Columns:
- `wallet`, `condition_id` (pk composite)
- `net_shares` (signed)
- `avg_entry_price` (numeric)
- `last_updated_at`

Populated from Data API `/positions` when requested or via periodic refresh for tracked wallets.

### `signal_events`

Materialized signals (alerts feed) with dedupe keys.

Columns:
- `id` (pk)
- `signal_type` (enum: `LARGE_NEW_WALLET_TRADE`, `ARB_BUY_BOTH`, `OI_SPIKE`, etc)
- `dedupe_key` (unique)
- `created_at`
- `severity` (int)
- `wallet` (nullable)
- `condition_id` (nullable)
- `payload` jsonb (stores computed fields and evidence)

Indexes:
- `(signal_type, created_at desc)`
- `(wallet, created_at desc)`
- `(condition_id, created_at desc)`

## Retention policy (defaults)

- `trades`: keep **forever** (it’s the core research artifact) unless storage becomes an issue.
- `market_metrics_ts`: keep 1-minute granularity for **30 days**, then downsample to hourly for **1 year**.
- `orderbook_levels`: keep only latest + optionally a sparse history (e.g., every 5 minutes for 7 days).
- `signal_events`: keep forever.

