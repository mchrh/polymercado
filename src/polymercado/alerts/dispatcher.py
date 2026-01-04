from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.models import AlertAck, AlertLog, AlertRule, AlertStatus, SignalEvent
from polymercado.utils import utc_now


def dispatch_alerts(session: Session, settings: AppSettings) -> int:
    if not settings.ALERTS_ENABLED:
        return 0

    default_channels = [
        c.strip() for c in settings.ALERT_CHANNELS.split(",") if c.strip()
    ]
    if not default_channels:
        return 0

    rules = []
    if settings.ALERT_RULES_ENABLED:
        rules = (
            session.execute(
                select(AlertRule)
                .where(AlertRule.enabled.is_(True))
                .order_by(AlertRule.priority.asc())
            )
            .scalars()
            .all()
        )

    sent = 0
    existing = select(AlertLog.signal_event_id).distinct().subquery()
    signals = (
        session.execute(select(SignalEvent).where(~SignalEvent.id.in_(existing)))
        .scalars()
        .all()
    )

    now = utc_now()

    for signal in signals:
        if signal.severity < settings.ALERT_MIN_SEVERITY:
            continue

        notification_key = build_notification_key(signal)
        if settings.ALERT_ACK_ENABLED and _is_acked(session, notification_key, now):
            _log_alert(
                session, signal, notification_key, AlertStatus.SUPPRESSED, "acked"
            )
            continue

        channels = default_channels
        cooldown_seconds = settings.ALERT_DEDUP_WINDOW_SECONDS
        if rules:
            matched = False
            for rule in rules:
                if rule_matches(rule.rule, signal, now):
                    actions = (rule.rule or {}).get("actions", {})
                    channels = actions.get("channels") or default_channels
                    if isinstance(channels, str):
                        channels = [channels]
                    cooldown_seconds = actions.get("cooldown_seconds", cooldown_seconds)
                    matched = True
                    break
            if not matched:
                continue

        dedupe_window = now - timedelta(seconds=cooldown_seconds)
        latest = (
            session.execute(
                select(AlertLog)
                .where(AlertLog.notification_key == notification_key)
                .order_by(AlertLog.sent_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if latest and latest.sent_at and latest.sent_at >= dedupe_window:
            if latest.severity is not None and latest.severity >= signal.severity:
                _log_alert(
                    session, signal, notification_key, AlertStatus.SUPPRESSED, None
                )
                continue

        for channel in channels:
            status, error = send_alert(channel, signal, settings)
            _log_alert(
                session, signal, notification_key, status, error, channel=channel
            )
            if status == AlertStatus.SENT:
                sent += 1

    session.commit()
    return sent


def build_notification_key(signal: SignalEvent) -> str:
    if signal.wallet:
        return f"{signal.signal_type}:{signal.wallet}"
    if signal.condition_id:
        return f"{signal.signal_type}:{signal.condition_id}"
    return f"{signal.signal_type}:{signal.id}"


def _is_acked(session: Session, notification_key: str, now: datetime) -> bool:
    row = (
        session.execute(
            select(AlertAck)
            .where(AlertAck.notification_key == notification_key)
            .order_by(AlertAck.acked_until.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return bool(row and row.acked_until and row.acked_until >= now)


def rule_matches(rule: dict[str, Any], signal: SignalEvent, now: datetime) -> bool:
    when = rule.get("when", {})
    if not when:
        return True

    signal_types = when.get("signal_type")
    if signal_types:
        if isinstance(signal_types, str):
            signal_types = [signal_types]
        if signal.signal_type not in signal_types:
            return False

    min_severity = when.get("min_severity")
    if min_severity is not None and signal.severity < int(min_severity):
        return False
    max_severity = when.get("max_severity")
    if max_severity is not None and signal.severity > int(max_severity):
        return False

    payload_min = when.get("payload_min", {})
    for key, threshold in payload_min.items():
        value = _payload_number(signal.payload, key)
        if value is None or value < float(threshold):
            return False

    payload_max = when.get("payload_max", {})
    for key, threshold in payload_max.items():
        value = _payload_number(signal.payload, key)
        if value is None or value > float(threshold):
            return False

    payload_eq = when.get("payload_eq", {})
    for key, expected in payload_eq.items():
        if signal.payload.get(key) != expected:
            return False

    quiet = when.get("quiet_hours")
    if quiet:
        start = quiet.get("start")
        end = quiet.get("end")
        if start is not None and end is not None and _in_quiet_hours(now, start, end):
            return False

    return True


def _payload_number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _in_quiet_hours(now: datetime, start_hour: int, end_hour: int) -> bool:
    hour = now.hour
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def send_alert(
    channel: str, signal: SignalEvent, settings: AppSettings
) -> tuple[AlertStatus, str | None]:
    message = format_message(signal)

    if channel == "log":
        return AlertStatus.SENT, None
    if channel == "slack":
        webhook = settings.ALERT_SLACK_WEBHOOK_URL
        if not webhook:
            return AlertStatus.FAILED, "missing_slack_webhook"
        try:
            httpx.post(webhook, json={"text": message}, timeout=10.0).raise_for_status()
            return AlertStatus.SENT, None
        except httpx.HTTPError as exc:
            return AlertStatus.FAILED, str(exc)
    if channel == "telegram":
        token = settings.ALERT_TELEGRAM_BOT_TOKEN
        chat_id = settings.ALERT_TELEGRAM_CHAT_ID
        if not token or not chat_id:
            return AlertStatus.FAILED, "missing_telegram_config"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        try:
            httpx.post(url, json=payload, timeout=10.0).raise_for_status()
            return AlertStatus.SENT, None
        except httpx.HTTPError as exc:
            return AlertStatus.FAILED, str(exc)

    return AlertStatus.FAILED, "unsupported_channel"


def format_message(signal: SignalEvent) -> str:
    payload = signal.payload
    prefix = f"[SEV{signal.severity}]"
    if signal.signal_type == "ARB_BUY_BOTH":
        edge = payload.get("edge_at_q_max")
        q_max = payload.get("q_max")
        edge_pct = None
        try:
            edge_pct = float(edge) * 100 if edge is not None else None
        except (TypeError, ValueError):
            edge_pct = None
        edge_label = f"{edge_pct:.2f}%" if edge_pct is not None else str(edge)
        return f"{prefix} Arb buy-both {edge_label} edge @ {q_max} shares"
    if signal.signal_type in {"LARGE_TAKER_TRADE", "LARGE_NEW_WALLET_TRADE"}:
        notional = payload.get("notional_usd")
        title = payload.get("market_title") or payload.get("market_slug")
        return f"{prefix} Trade ${notional} {title}"
    return f"{prefix} {signal.signal_type}"


def _log_alert(
    session: Session,
    signal: SignalEvent,
    notification_key: str,
    status: AlertStatus | str,
    error: str | None,
    channel: str | None = None,
) -> None:
    if isinstance(status, str):
        status = AlertStatus.SUPPRESSED
    log = AlertLog(
        signal_event_id=signal.id,
        channel=channel or "dedupe",
        notification_key=notification_key,
        sent_at=utc_now(),
        status=status,
        severity=signal.severity,
        error=error,
    )
    session.add(log)
