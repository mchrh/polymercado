# Overview

## Problem statement

We want a web dashboard/research platform that turns Polymarket public data into **actionable signals**:

- “Who is trading big, and are they new?”
- “Is there executable arb right now?”
- “Which markets in a category are actually liquid/interesting?”

The platform should be **configurable**, **auditable** (show why a signal fired), and **fast enough** for research (seconds-to-minutes, not microseconds).

## Key definitions

### Wallet identity

Polymarket users often trade through a **proxy wallet** (especially Gnosis Safe proxy). The Data API exposes `proxyWallet` on trades/positions.

- **Wallet (canonical for this platform):** `proxyWallet` when present; else fallback to `user` address (if returned by an endpoint).
- All “new wallet” logic uses this canonical wallet.

### “New wallet”

In v1, “new” means **new to our observation**, not “new on-chain”:

- `first_seen_at`: first time the platform observes a canonical wallet in a trade stream.
- `new_wallet_window`: how long after `first_seen_at` we still consider it “new”.
  - Default: **14 days**.

Notes:
- You can later add an on-chain age estimator (first tx time) via Polygon RPC, but that’s out-of-scope for v1.

### “Sizeable trade” (USD)

We use Data API `/trades` filtering in **cash terms**:
- Default threshold: **$10,000** notional.
- We only include **taker prints**: `takerOnly=true` by default.

### “Arbitrage opportunity”

Binary markets pay out $1 to the winning outcome. Classic “buy both sides” arb is:

- **Buy YES + Buy NO** total cost per $1 payout \< `1 - edge_min`
  - Default `edge_min = 0.01` (so \< 0.99).

We require **executable, size-aware** detection:

- Evaluate the sum of **effective average ask prices** for buying `q` shares of YES and `q` shares of NO using orderbook depth.

### Liquidity proxies

We expose multiple liquidity indicators (none is perfect alone):

- Gamma:
  - `volume` / `volumeNum` (indexed; time window depends on Polymarket’s definition)
  - `liquidity` / `liquidityNum`
- Data API:
  - **Open interest** via `/oi` (per conditionId)
- CLOB:
  - Spread, top-of-book sizes, depth within X cents (derived from `/book`)

## Assumptions (explicit)

- We are building a **research tool**, not a trading bot.
- We can rely on **public endpoints**; no user private keys required.
- Data freshness targets:
  - Trades: **~30–60s**
  - Orderbooks: **~1–5s** for arb (via websocket preferred), else 10–30s polling
  - Markets metadata: **~5–15 min**
  - Open interest: **~5 min**
- Storage: Postgres as the default (can be swapped later).
- Deployment: single node is fine for v1; multi-worker scaling is planned.

## Open questions (not blockers for spec)

1. Hosting: local-only vs hosted SaaS? (Affects auth and rate-limiting strategy.)
2. Alert destinations: Slack, Telegram, email? (We’ll spec a pluggable interface.)
3. Do you want “new wallet” relative to *all time* or “newly active after dormancy”? (We spec both.)

