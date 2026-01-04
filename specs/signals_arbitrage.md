# Signals: executable binary arbitrage

## Purpose

Identify **executable** arbitrage opportunities in **binary markets** by evaluating:

1. Top-of-book (fast screening).
2. Depth-aware (size-aware) “buy both sides” cost.

Primary signal:
- `ARB_BUY_BOTH` when buying YES and NO in equal shares costs \< `1 - edge_min` per share.

## Scope

- v1 only evaluates arb **within a single binary market** (YES vs NO tokens for the same conditionId).
- v1 does **not** attempt cross-market neg-risk conversion arbs (explicitly out-of-scope; complex but addable later).

## Inputs

- Latest orderbook levels for YES token and NO token:
  - From websocket-maintained cache flushed to `orderbook_levels` OR directly from `GET /book`.
- Market metadata:
  - YES token id, NO token id, tick size, min order size, negRisk.

## Config (defaults)

- `edge_min`: `0.01` (i.e., require cost \< 0.99 per $1 payout)
- `min_executable_shares`: `50` shares (ignore tiny arb)
- `max_shares_to_evaluate`: `5000` shares (cap computation)
- `max_age_seconds_book`: `10` (ignore stale books; severity depends on freshness)
- `cooldown_seconds_per_market`: `60` (don’t emit duplicate arb signals every tick)

## Core computations

### Orderbook normalization

We assume book levels are aggregated:
- `asks`: best→worst increasing price
- `bids`: best→worst decreasing price
Each level: `{price: str, size: str}`.

Convert to:
- `List[Level(price: Decimal, size: Decimal)]`
- Drop levels with non-positive price/size.

### Effective average ask price for `q` shares

For a token with asks `[(p1,s1), (p2,s2), ...]`:

- Fill shares greedily:
  - `fill_i = min(remaining, s_i)`
  - `cost += fill_i * p_i`
  - `remaining -= fill_i`
- If `remaining > 0`, book is insufficient for size `q` ⇒ not executable at that size.

Define:
- `avg_ask(q) = cost / q`
- `max_fillable = sum(s_i)`

### Arb condition (buy both sides)

For equal share size `q`:

- `total_avg_cost(q) = avg_ask_yes(q) + avg_ask_no(q)`
- Arb if: `total_avg_cost(q) < 1 - edge_min`

Compute:
- `q_max`: maximum `q` such that condition holds and is fillable on both sides.
- `edge(q) = 1 - total_avg_cost(q)` (positive is “free money” before fees/slippage)

### Fast screen (top-of-book)

Let:
- `best_ask_yes = asks_yes[0].price`
- `best_ask_no = asks_no[0].price`

Top-of-book arb if:
- `best_ask_yes + best_ask_no < 1 - edge_min`

This is only a screen; the signal should include depth-aware numbers.

## Signal emission

### `ARB_BUY_BOTH`

Trigger:
- Market is binary (has exactly two outcomes and both token IDs known).
- Books are fresh (or at least not too stale).
- There exists `q >= min_executable_shares` such that arb condition holds.

Payload (minimum):
- `condition_id`
- `yes_token_id`, `no_token_id`
- `as_of_yes`, `as_of_no` (book timestamps)
- `best_ask_yes`, `best_ask_no`, `top_of_book_sum`
- `edge_min`, `min_executable_shares`
- `q_max`
- `edge_at_min_q` (edge at `min_executable_shares` if fillable)
- `edge_at_q_max`
- `avg_ask_yes_at_q_max`, `avg_ask_no_at_q_max`
- Evidence:
  - top N ask levels used on each side (prices/sizes up to q_max)

Dedupe key:
- `ARB_BUY_BOTH:{condition_id}:{round(edge_at_q_max,4)}:{round(q_max,2)}`
- Apply per-market cooldown to avoid spam.

Severity (suggested):
- Based on `edge_at_q_max` and `q_max`:
  - edge >= 1.5% and q_max >= 500 shares ⇒ high
  - edge >= 1.0% and q_max >= 100 shares ⇒ medium
  - else low
- Add penalty if books are stale (reduce severity).

## Practical considerations

### Fees

Docs currently indicate 0 bps, but this can change.

We should treat fees as config:
- `taker_fee_bps = 0` default

Then require:
- `total_avg_cost(q) + fee_model(q) < 1 - edge_min`

### Settlement constraints

Even if arb exists:
- You still need capital to buy both sides.
- Execution can move the book; our size-aware method approximates slippage using visible depth, but not hidden dynamics.

### Neg-risk / augmented neg-risk

Within-market YES/NO arb is still valid mechanically, but:
- In some neg-risk contexts, “NO” may have additional convertibility meaning.

v1 behavior:
- Still compute arb for neg-risk markets, but annotate `neg_risk=true` in payload and allow filtering them out in UI.

## UI requirements

### Arb screener

Table:
- Market, tags, `edge_at_min_q`, `q_max`, `edge_at_q_max`, book freshness, top-of-book sum

Drilldown:
- Show the two books’ asks used in computation.
- Show “what size can you do at edge >= X”.

