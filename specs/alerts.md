# Alerts: routing, dedupe, and operator workflow

## Goals

- Convert `signal_events` into actionable notifications.
- Avoid spam: dedupe and cooldowns.
- Support multiple channels via a clean interface.

## Alert lifecycle

### Emission

The signal engine writes a row to `signal_events`.

### Routing

An `alert_dispatcher` job reads undispatched signals and routes them to configured channels based on rules.

### Acknowledgement (optional v1)

Operators can “ack” a signal to silence repeats for a period.

## Alert rules

Rules are evaluated in order and can:
- set severity thresholds
- filter signal types
- route to channels
- apply cooldown/quiet hours

Example rule:
- If `signal_type == ARB_BUY_BOTH` and `edge_at_q_max >= 0.015` and `q_max >= 500` → send to Slack `#arbs-high`.

## Dedupe strategy

### Signal-level dedupe

Already handled via:
- `signal_events.dedupe_key` unique constraint
- per-market cooldowns (arb)

### Notification-level dedupe

Even if distinct signals exist, we may still want a notification dedupe window:

- Compute `notification_key = signal_type + ':' + (wallet or condition_id)`.
- If a notification with same key was sent in the last `ALERT_DEDUP_WINDOW_SECONDS`, suppress unless severity increased.

Persist in `alert_log` table (proposed):
- `id` pk
- `signal_event_id` fk
- `channel`
- `notification_key`
- `sent_at`
- `status` (`SENT|FAILED|SUPPRESSED`)
- `error` (nullable)

## Channel payloads

### Slack (recommended)

Message format:
- Title line: `[SEV3] Arb buy-both 1.3% edge @ 800 shares`
- Fields: market title, tags, edge/q_max, link to market detail page, timestamp.

### Email

Digest-friendly:
- Batch alerts into 5-minute windows per channel.

### Telegram

Short-format message with deep links.

## Operator UX

- Alerts page:
  - list of recent signals
  - filters by type/severity
  - “Ack for 1h/24h” button
- Alert rules page:
  - edit routing rules and thresholds (stored in DB)

## Failure modes

- Downstream webhook failure:
  - mark `FAILED` and retry with backoff
  - stop retrying after N attempts; surface in UI
- Dispatch lag:
  - show queue length and oldest undelivered signal

