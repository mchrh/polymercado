# Polymarket Research Dashboard — Specifications

Othmane, this folder defines an implementation-ready spec for a Python-first web dashboard / research platform built on Polymarket public APIs.

## Goals (v1)

1. Detect **new wallets** making **sizeable taker trades** (size threshold in **USD**, not shares).
2. Detect **executable binary arbitrage** opportunities (e.g., **buy YES + buy NO** cost \< `0.99` per $1 payout), **size-aware** using orderbook depth.
3. Explore / filter markets by **category** and **liquidity proxies** (Gamma volume/liquidity + Data API open interest).

## Non-goals (v1)

- Placing orders / automated trading.
- Private endpoints requiring user keys.
- Perfect historical completeness (we aim for “best effort” with backfills).

## Document index

0. `specs/overview.md` — product requirements, definitions, assumptions
1. `specs/architecture.md` — system architecture (Python), components, contracts
2. `specs/data_sources.md` — APIs used, endpoints, mappings, rate-limit strategy
3. `specs/storage.md` — database schema, retention, indexes
4. `specs/ingestion.md` — ingestion pipeline, scheduling, idempotency, backfills
5. `specs/signals_wallets.md` — new-wallet + large-taker-trade detection
6. `specs/signals_arbitrage.md` — arb detection (top-of-book + depth-aware)
7. `specs/markets_explorer.md` — market filtering/ranking (tags, liquidity, OI)
8. `specs/alerts.md` — alerts, routing, dedupe, escalation
9. `specs/ui.md` — dashboard UX + pages + drilldowns
10. `specs/config.md` — configuration model and defaults
11. `specs/ops_testing.md` — observability, testing strategy, failure modes
