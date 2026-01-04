# Signals: wallets & large taker trades

## Purpose

Detect and rank:

1. **Large taker trades** (USD-based).
2. **Large taker trades by new wallets**.
3. **Dormant wallet reactivation** (optional in v1 but easy).

The output is a stream of `signal_events` with drilldown evidence and dedupe guarantees.

## Data inputs

- Data API `GET /trades` (primary)
  - `takerOnly=true`
  - `filterType=CASH`
  - `filterAmount=<large_trade_usd_threshold>`
- Gamma (for enriching trade context)
  - Map `conditionId → slug/title/tags/event`
- Optional:
  - Data API `GET /positions?user=...` for tracked wallets when a large trade hits

## Config (defaults)

### Thresholds

- `large_trade_usd_threshold`: `10000` (USD)
- `new_wallet_window_days`: `14`
- `dormant_window_days`: `30` (no trades observed)
- `wallet_track_after_large_trade_days`: `7` (refresh positions for a week)

### Severity mapping (suggested)

- Base severity uses notional bands:
  - $10k–$50k: severity 2
  - $50k–$250k: severity 3
  - $250k–$1m: severity 4
  - \> $1m: severity 5
- Add +1 if wallet is “new”.
- Add +1 if this trade is in a market below a liquidity floor (more “signal-y”).

## Canonical wallet identity

From Data API `/trades`:
- Prefer `proxyWallet` (canonical)
- If `proxyWallet` is missing (should be rare): fallback to `user` or `owner` if present.

Store canonical wallet in:
- `trades.wallet`
- `signal_events.wallet`

## Derived fields

For each ingested trade print:

- `notional_usd = price * size`
- `directional_notional_usd`:
  - BUY: `+notional_usd`
  - SELL: `-notional_usd` (useful for net flow metrics)

## Signal types

### A) `LARGE_TAKER_TRADE`

Trigger condition:
- Any trade ingested by `sync_large_trades` (already thresholded) OR trades above a lower threshold if we later broaden ingestion.

Payload (minimum):
- `wallet`
- `trade_ts`
- `condition_id`, `token_id`
- `side`, `size_shares`, `price`, `notional_usd`
- `market_slug`, `market_title`, `event_slug`, `outcome`
- `tx_hash` (if present)
- `wallet_first_seen_at`, `wallet_age_days`
- `market_liquidity`, `market_volume`, `market_open_interest` (latest snapshots if available)

Dedupe:
- Primary: `transaction_hash` if present.
- Else: hash of `(wallet, condition_id, token_id, side, trade_ts, size, price)`.

### B) `LARGE_NEW_WALLET_TRADE`

Trigger condition:
- `LARGE_TAKER_TRADE` AND `trade_ts <= wallet.first_seen_at + new_wallet_window_days`

Notes:
- If a wallet first appears with a large trade, both signals will fire (or the “new wallet” one can supersede).

Dedupe:
- Reuse the same underlying trade dedupe key, but prefix by signal type.

### C) `DORMANT_WALLET_REACTIVATION` (optional)

Trigger condition:
- Wallet has not traded in `dormant_window_days` AND makes a large trade.

Rationale:
- Sometimes the interesting event is not “new wallet” but “known wallet woke up”.

## Wallet state updates

On every ingested trade:

- If wallet not in `wallets`, insert:
  - `first_seen_at = now()`
  - `first_trade_ts = trade_ts`
- Always:
  - `last_seen_at = now()`
  - increment `lifetime_notional_usd += notional_usd`

Optional rollups:
- Maintain a rolling 7d notional by recomputing nightly or using an incremental window.

## Position refresh for tracked wallets (optional in v1)

When a large trade fires:
- Mark wallet as “tracked until” `now + wallet_track_after_large_trade_days`.
- A job runs every N minutes to refresh positions for tracked wallets via `/positions?user=...`.

Use cases:
- UI wallet page shows “what else they hold” and “net exposure by outcome/category”.

## UI requirements (drilldowns)

### “Whale tape”

Table with:
- Time, wallet, notional, side/outcome, market, tags, liquidity/OI
- Filters: min notional, new-only, category, market, wallet, time window

### Wallet page

- Summary: first seen, last seen, lifetime notional, last 7d notional
- Recent trades (last N)
- Current positions (if enabled), sortable by size/currentValue/PNL

## Edge cases & safeguards

- Data API timestamp ordering: don’t assume monotonic; use a safety window when paging.
- Wallet “newness” is platform-relative; always show `first_seen_at` in UI.
- Avoid spam: allow per-market or per-wallet cooldowns in alerting layer.

