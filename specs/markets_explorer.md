# Markets explorer: filtering, ranking, and discovery

## Purpose

Provide an interface to find markets worth researching by combining:

- Category filters (tags/sports/series/search).
- Liquidity proxies (Gamma volume/liquidity, Data API open interest).
- Market microstructure (spread/depth from CLOB).

## Inputs

- Gamma:
  - `/events` and/or `/markets`
  - `/tags`, `/sports`, `/series`
- Data API:
  - `/oi`
- CLOB:
  - `/book` or `/books` for tracked markets

## Derived metrics (per market)

### Liquidity composite score (v1 heuristic)

We will expose raw metrics and a composite:

- `gamma_volume` (normalized)
- `gamma_liquidity`
- `open_interest`
- `spread_mid`:
  - `(best_ask - best_bid)` for YES and NO
- `depth_within_1c`:
  - shares available within 0.01 of best ask/bid (configurable)

Composite (example):

```
score = w1 * log1p(volume) + w2 * log1p(liquidity) + w3 * log1p(open_interest)
        - w4 * spread_penalty
```

Default weights:
- `w1=1.0`, `w2=1.0`, `w3=1.5`, `w4=0.5`

This is for ranking only; users can sort by any underlying metric.

### Market status flags

- `active`, `closed` (from Gamma)
- `ends_soon` (end_time within N days)
- `neg_risk`, `neg_risk_augmented` (if available)

## Filtering capabilities

### By category

- tag include/exclude:
  - include: `tag_id`
  - include related: `related_tags=true`
  - exclude: `exclude_tag_id[]` (events endpoint supports)
- sports:
  - filter by `series_id` or known sports tag IDs (from `/sports`)
- text search:
  - Gamma `/search` (preferred)
  - fallback: local `ILIKE` on title/slug if already ingested

### By liquidity / activity

- `gamma_volume >= X`
- `gamma_liquidity >= Y`
- `open_interest >= Z`
- `spread <= S`
- “newest markets first” using event/market IDs

## “New markets” detection

We define “new market” as:
- A market `condition_id` not previously seen in `markets`.

Process:
- On each `sync_gamma_events`, compare discovered `condition_id`s to DB.
- Emit `signal_events` type `NEW_MARKET` for newly discovered ones.

Payload:
- `condition_id`, `slug`, `title`, `tags`, `start/end`, `token_ids`
- initial metrics if present (volume/liquidity)

## UI requirements

### Markets table

Columns:
- title/slug, tags, end date, volume, liquidity, open interest, spread, depth, neg-risk flag

Controls:
- tag picker (with related-tags toggle)
- numeric range sliders for volume/liquidity/OI/spread
- sort dropdown (composite, volume, OI, newest, tightest spread)

Drilldown:
- Market detail page:
  - current books, price history stub (later), large-trade tape for this market
  - “wallets active here” (top wallets by notional in last 24h/7d)

## Future extensions (explicitly not required for v1)

- Price history / realized volatility using CLOB pricing endpoints or snapshots.
- UMA resolution status + clarifications surfaced in UI.
- Cross-market neg-risk conversion analytics.

