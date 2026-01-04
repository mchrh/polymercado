# Ingestion pipeline & scheduling

## Ingestion modes

### 1) Polling (default for v1)

Used for:
- Gamma events/markets metadata
- Data API trades + OI
- CLOB /book snapshots for a selected universe

### 2) WebSocket-driven (recommended for arb freshness)

Used for:
- CLOB market channel orderbook updates for tracked token IDs

## Universe selection (what we track)

We can’t track every orderbook at 1s cadence cheaply; define a “tracked universe”:

1. Start from Gamma: active, not closed events.
2. Filter markets by liquidity proxy:
   - `gamma_liquidity >= min_liquidity` OR `gamma_volume >= min_volume` OR `open_interest >= min_oi`
3. Cap to `max_tracked_markets` (default 200) and update every 10–15 minutes.
4. Always include manual overrides from `tracked_markets` (up to the cap).

This universe drives websocket subscriptions and/or polling for `/book`.

## Jobs (default schedule)

### `sync_gamma_events`

- Frequency: every **10 minutes**
- Pull: `GET /events?active=true&closed=false&order=id&ascending=false&limit=100&offset=...`
- Upsert into `markets` (and any `events` table if added later).
- Detect newly discovered markets/events.
- Normalize Gamma fields:
  - Parse `outcomes` and `outcomePrices` JSON strings into arrays.
  - Handle `clobTokenIds` as array or JSON string.
  - Prefer numeric `volumeNum`/`liquidityNum` when present.

### `sync_tag_metadata`

- Frequency: every **6–12 hours**
- Pull:
  - `GET /tags` for tag labels/slugs
  - `GET /sports` for sports tag mapping
- Upsert into `tags` and mark `is_sport` where relevant.

### `sync_open_interest`

- Frequency: every **5 minutes**
- For the tracked universe condition IDs, call `GET /oi?market=...` in batches.
- Upsert `market_metrics_ts` snapshot (`open_interest`).

### `sync_large_trades`

Goal: create a near-real-time feed of large taker prints.

- Frequency: every **30–60 seconds**
- Call Data API `GET /trades`:
  - `takerOnly=true`
  - `filterType=CASH`
  - `filterAmount=<large_trade_usd_threshold>`
  - `limit=<page_size>` (e.g., 500)
  - `offset=0`
- We treat it as a rolling window:
  - keep fetching pages until we reach trades older than our `last_trade_ts_seen - safety_window`.
  - if we have no local trades yet, only fetch up to `TRADES_INITIAL_LOOKBACK_HOURS`.
  - cap work per run with `TRADES_MAX_PAGES` to keep the scheduler responsive.
- Insert trades idempotently:
  - Dedupe by `transaction_hash` if present; else by `(wallet, condition_id, token_id, side, trade_ts, size, price)`.

### `sync_orderbooks` (polling fallback)

- Frequency: every **10–30 seconds** if websocket is not enabled.
- For each tracked token ID, call `GET /book?token_id=...` (or batch via `POST /books`).
- Update `orderbook_levels`.
- Normalize timestamps:
  - REST `/book` uses RFC3339 strings.
  - WebSocket uses millisecond epoch strings.
- Normalize negative risk:
  - Prefer CLOB `neg_risk` when orderbooks are present.
  - Fallback to Gamma `negRisk` for markets without books.

### `run_signal_engine`

- Frequency: every **30–60 seconds**
- Reads newest `trades`, latest `orderbook_levels`, `market_metrics_ts`.
- Writes `signal_events`.

## Idempotency & correctness guarantees

### Trade ingestion

Guarantees:
- “At least once” ingestion with strong dedupe.
- Late-arriving trades won’t create duplicates.

### Book ingestion

Websocket is “best effort”; we heal via periodic snapshots:
- Every N minutes per token (default 5), force refresh from `/book`.
- If hashes mismatch wildly or sequence is unknown, resubscribe.

## Backfills

### Trades backfill

For a given time window (e.g., last 30 days), page through `/trades` without `filterAmount`, store everything or store only above some lower threshold.

Note: Data API pagination is offset-based; large offsets may be slow. Backfill should be an offline job with pacing.

### Markets backfill

Page Gamma `/events` historically by `closed=true` or by date ranges as needed.

## Failure handling

- Retry with exponential backoff for 5xx and timeouts.
- For 429/throttle: slow down concurrency, increase interval.
- Persist “job run” metadata (optional table) for audit.
