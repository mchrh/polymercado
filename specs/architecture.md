# Architecture (Python-first)

## Summary

Design for correctness and auditability first:

- **Ingestion** pulls/pushes public data from Gamma/CLOB/Data APIs.
- **Storage** persists normalized entities (markets, tokens, trades, wallets) plus time-series snapshots.
- **Signal engine** materializes “events” (large new-wallet trades, arb windows, spikes) into queryable tables.
- **Web app** serves dashboards + drilldowns + alert configuration.

## Proposed stack (opinionated default)

### Backend + UI

- **FastAPI**: API + server-side rendered UI.
- **Jinja2 + HTMX**: keeps “web dashboard” in Python while staying interactive.
  - Minimal JS.
  - If we later outgrow it, the API can support a React frontend without rewriting ingestion.

### Ingestion + compute

- **APScheduler** for v1 scheduling (simple, in-process).
  - Upgrade path: Celery/RQ/Arq for distributed workers if needed.
- **asyncio** + `httpx` for API calls.
- **websockets** (or `websockets`/`aiohttp`) for CLOB market channel consumption.

### Storage

- **Postgres**:
  - relational core entities + indexed time-series tables
  - enables rich SQL for research queries
- Optional: TimescaleDB later if time-series volume grows.

### Data validation

- **Pydantic** models for request/response validation where useful.
- Store raw payload JSON for auditability (selectively).

## Component diagram

```
          +-------------------+
          | Polymarket APIs   |
          | Gamma / CLOB /    |
          | Data / WebSocket  |
          +---------+---------+
                    |
          (polling + websocket)
                    |
                    v
        +------------------------+
        | Ingestion Service      |
        | - fetchers             |
        | - parsers              |
        | - dedupe/idempotency   |
        +-----------+------------+
                    |
                    v
        +------------------------+
        | Postgres               |
        | - entities             |
        | - time-series snapshots|
        | - signal events        |
        +-----------+------------+
                    |
                    v
        +------------------------+
        | Signal Engine          |
        | - wallet novelty       |
        | - arb evaluator        |
        | - market ranking       |
        +-----------+------------+
                    |
                    v
        +------------------------+
        | FastAPI Web App        |
        | - dashboards           |
        | - drilldowns           |
        | - alert config         |
        +------------------------+
```

## Key design principles

- **Idempotent ingestion**: re-running should not duplicate trades/signals.
- **Explainability**: each alert/signal stores the computed fields used to fire.
- **Config-driven**: thresholds are runtime config (DB + env), no code edits.
- **Rate-limit aware**: adaptive pacing; prefer batching endpoints.
- **Separation**: ingestion writes raw/normalized; signal engine reads and writes signal tables.

## Security / production safety

- No private keys in v1.
- Secrets limited to:
  - DB connection
  - alert webhooks (if used)
- Add an auth layer later if hosted (basic auth / OAuth), but spec keeps it optional.

