# Data sources & API contracts

This section lists the endpoints we will rely on and how we will use them.

## Base URLs

- Gamma API: `https://gamma-api.polymarket.com`
- CLOB API: `https://clob.polymarket.com`
- Data API: `https://data-api.polymarket.com`
- CLOB WebSocket: `wss://ws-subscriptions-clob.polymarket.com/ws/`
- RTDS: `wss://ws-live-data.polymarket.com` (optional, not required for v1 goals)

## Rate limits (design constraints)

Cloudflare throttles (queues/delays) rather than immediately rejecting.

We will:
- Use **batch endpoints** where available (`/prices`, `/books`) for breadth views.
- Prefer websocket for high-frequency orderbook updates.
- Implement client-side pacing + backoff for 429/5xx.

## Gamma API (market discovery & metadata)

Primary use: market explorer + category filters + “new market” detection.

### `GET /events`

Use cases:
- Discover all active events and the markets contained within them.

Recommended query defaults:
- `active=true`
- `closed=false`
- `limit=100` (or smaller) and paginate with `offset`.
- Sort: `order=id&ascending=false` for “newest first”.

Fields we depend on (examples; exact naming depends on Gamma schema):
- event: `id`, `slug`, `title`, `tags[]`, `active`, `closed`, `startDate/endDate`
- market (nested): `id`, `question`, `conditionId`, `clobTokenIds`, `outcomes`, `outcomePrices`, `volumeNum`, `liquidityNum`, `negRisk`

Gamma payload normalization:
- `outcomes` and `outcomePrices` are JSON-encoded strings in practice; parse into arrays.
- `clobTokenIds` is usually an array; handle a JSON-encoded string fallback.
- Prefer numeric `volumeNum`/`liquidityNum`; `volume`/`liquidity` may be strings.
- Negative risk flags vary by API: Gamma `negRisk`, CLOB `neg_risk`, Data API positions `negativeRisk`. Normalize to `neg_risk`.

### `GET /markets`

Use cases:
- Fetch a market by slug/id, or list markets filtered by `tag_id`, `closed`, etc.

Also useful fields (if present):
- `min_incentive_size`, `max_incentive_spread` for liquidity-reward aware views.

### `GET /tags`, `GET /sports`, `GET /series`, `GET /public-search`

Use cases:
- Build category taxonomy and support UI filtering.

## Data API (wallet-centric research)

Primary use: large trades + wallet behavior + open interest.

### `GET /trades`

Use cases:
- Primary feed for “large taker trades”.

Key query params:
- `takerOnly=true` (default)
- `filterType=CASH` and `filterAmount=<usd_threshold>` (default $10,000)
- `limit`/`offset` pagination
- Optional filters:
  - `market=<conditionIds>` for drilldowns
  - `eventId=<eventIds>` (Data API expects integers; Gamma event IDs are strings, cast carefully)
  - `user=<address>` for wallet pages
  - `side=BUY|SELL`

Fields we depend on:
- `proxyWallet` (canonical wallet)
- `conditionId`
- `asset` (tokenId)
- `side`, `size`, `price`
- `timestamp` (ms epoch)
- market context: `title`, `slug`, `eventSlug`, `outcome`, `outcomeIndex`
- `transactionHash` (useful for audit)
Notes:
- Data API trade payloads only document `proxyWallet` for identity; do not assume `user`/`owner` fields exist.

### `GET /positions`

Use cases:
- Wallet exposure views, mergeable/redeemable flags.

Key query params:
- `user=<address>` required
- `redeemable`, `mergeable` booleans
- sort options (TOKENS/CASHPNL/etc)

### `GET /oi`

Use cases:
- Open interest by `market` (conditionId) to rank/filter markets.

Notes:
- Endpoint accepts a list of `market` condition IDs; we should batch requests.
- Response items are `{market, value}`; map `value` to `open_interest`.

### Optional Data API endpoints (future)

- `/activity` for richer wallet feeds.
- builder leaderboard endpoints if we ever care about routed flow.

## CLOB API (pricing and orderbook)

Primary use: arb evaluator, spreads, depth.

### `GET /book?token_id=...`

Use cases:
- Full depth snapshot (bids/asks levels aggregated).

Fields:
- `bids[]`, `asks[]` with string `price` and `size`
- `tick_size`, `min_order_size`, `neg_risk`, `market` (conditionId), `asset_id` (tokenId)

### `GET /price?token_id=...&side=BUY|SELL`

Use cases:
- Quick top-of-book pricing when we don’t need depth.

### Optional: `POST /books`, `GET /prices`, `POST /prices`, `GET /midpoint`

Use cases:
- High-cardinality refreshes when scanning many markets.

## CLOB WebSocket (market channel)

Primary use: keep orderbooks fresh for arb without aggressive polling.

Channel: `market`
- Subscribe by `assets_ids` (token IDs).
- Messages include:
  - `book`: full snapshot
  - `price_change`: incremental changes (breaking change note in docs; design for schema evolution)

Design:
- Maintain in-memory orderbook cache keyed by `asset_id`.
- Periodically resync with `GET /book` snapshots to heal missed updates.
Notes:
- `book` messages use `bids`/`asks` arrays; docs sometimes label them as `buys`/`sells`.
- WS timestamps are millisecond strings; REST `/book` timestamps are RFC3339.
