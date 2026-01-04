# Ops, observability, and testing

## Operational goals

- The platform should degrade gracefully when an upstream endpoint throttles.
- We must be able to answer: “is data fresh?”, “what jobs are failing?”, “are signals firing correctly?”

## Observability

### Logging

Structured logs (JSON) with:
- `component` (ingestion/signal/web)
- `job_name`
- `request_id`
- `upstream` (gamma/clob/data/ws)
- `duration_ms`
- `status_code` / exception

### Metrics (minimal)

Expose `/metrics` (Prometheus style) or a simple `/healthz` + `/status` page.

Key metrics:
- last successful run time per job
- request counts by upstream and status code
- websocket connected? subscribed token count?
- DB write latency
- signal counts by type (last hour/day)

### Health checks

- `/healthz`:
  - DB connectivity
  - scheduler running
- `/readyz`:
  - “caught up” indicator (trade lag < N minutes, book lag < N seconds for tracked universe)

## Data quality checks

Nightly (or hourly) sanity checks:
- For each market in tracked universe: we have two token IDs and both books present.
- No absurd prices (outside [0,1]) in books.
- Trades notional recompute matches stored within epsilon.
- “New wallet” counts not exploding (detect ingestion loops).

## Testing strategy (no network dependency)

### Unit tests

- Orderbook depth math:
  - `avg_ask(q)` correctness with partial fills
  - `q_max` computation
- Trade dedupe key generation and idempotency
- Wallet novelty/dormancy logic

### Integration tests (with fixtures)

Record a few representative JSON payloads from:
- Gamma `/events`
- Data `/trades`
- CLOB `/book`

Then test parsing + DB upserts using a temporary Postgres (or sqlite with adapters, but Postgres is preferred for parity).

### Property-based tests (optional)

- Generate random orderbooks and verify:
  - `avg_ask(q)` is non-decreasing in `q`
  - `q_max` respects fillable depth bounds

## Failure modes and mitigations

- **Upstream throttling:** reduce concurrency, increase interval, keep running with stale-but-marked data.
- **Websocket disconnects:** reconnect with backoff; resubscribe; trigger snapshot heals.
- **Schema drift:** validate critical fields; store raw payload and log unknown fields.
- **DB contention:** batch inserts; use upserts; keep indexes minimal initially.

## Developer workflow (Python)

When implementation starts, standardize on:
- `uv` for environment + dependency management
- `ruff` for lint/format

Recommended commands (to be added later as scripts):
- `uv venv && uv sync`
- `ruff check . && ruff format .`
- `pytest`

