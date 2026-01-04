# UI / UX specification

## Principles

- Everything should be explainable (“why did this row show up?”).
- Workflows should be: **scan → filter → drilldown → save view → alert**.
- Default views are opinionated but fully configurable.

## Navigation

Left nav:
- Markets
- Arb screener
- Whale tape
- Wallets
- Alerts
- Config (admin)

## Pages

### 1) Markets

Primary table view with filters:
- Tags include/exclude, related-tags toggle
- Active/closed
- Volume/liquidity/OI sliders
- Spread/depth filters (if computed)

Columns:
- Title, tags, end date
- Volume, liquidity, open interest
- Spread (YES/NO), depth within 1c (optional)
- Neg-risk flag

Actions:
- Click → Market detail
- “Add to tracked universe” (manual override)

### 2) Market detail

Sections:
- Summary: title, tags, dates, neg-risk, token IDs
- Orderbooks: YES and NO (latest snapshot), with “as of”
- Arb panel: current computed edges for q = [50, 100, 500, 1000]
- Trades tape (market-scoped): last N large trades
- Wallet activity: top wallets by notional (last 24h/7d)

### 3) Arb screener

Table sorted by edge or executable size:
- condition, title, tags
- `edge_at_min_q`, `q_max`, `edge_at_q_max`
- top-of-book sum, book freshness

Drilldown shows:
- levels used for q_max
- “edge vs size” curve (optional; can be a simple table in v1)

### 4) Whale tape (large taker trades)

Table of `LARGE_TAKER_TRADE` and/or `LARGE_NEW_WALLET_TRADE`:
- time, wallet, notional, market/outcome, tags, liquidity/OI

Filters:
- min notional
- new-only toggle
- tag filter
- market filter

### 5) Wallets

List page:
- sort by first seen, last seen, lifetime notional, last 7d notional
- filter: “new wallets” (first seen within N days)

Wallet detail:
- summary stats
- recent large trades
- positions (if enabled), sortable

### 6) Alerts

Recent signal stream with:
- status (sent/suppressed/failed)
- ack controls
- rule editor link

### 7) Config

Editable configuration keys with validation and “last updated by”.

## Deep linking

Every row should link to a stable URL:
- `/markets/{condition_id}`
- `/wallets/{wallet}`
- `/signals/{signal_event_id}`

## Evidence & explainability requirements

Every signal detail page must show:
- the raw computed numbers (edge, q_max, notional, etc)
- timestamps and source freshness
- a small excerpt of the underlying data (book levels or trade payload)

