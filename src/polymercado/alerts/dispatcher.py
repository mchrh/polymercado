from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.models import AlertLog, AlertStatus, SignalEvent
from polymercado.utils import utc_now


def dispatch_alerts(session: Session, settings: AppSettings) -> int:
    if not settings.ALERTS_ENABLED:
        return 0

    channels = [c.strip() for c in settings.ALERT_CHANNELS.split(",") if c.strip()]
    if not channels:
        return 0

    sent = 0
    existing = select(AlertLog.signal_event_id).distinct().subquery()
    signals = (
        session.execute(select(SignalEvent).where(~SignalEvent.id.in_(existing)))
        .scalars()
        .all()
    )

    dedupe_window = utc_now() - timedelta(seconds=settings.ALERT_DEDUP_WINDOW_SECONDS)

    for signal in signals:
        if signal.severity < settings.ALERT_MIN_SEVERITY:
            continue
        notification_key = build_notification_key(signal)
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
    title = f"[{signal.signal_type}] severity {signal.severity}"
    if signal.signal_type == "ARB_BUY_BOTH":
        edge = payload.get("edge_at_q_max")
        q_max = payload.get("q_max")
        title = f"[ARB] edge {edge} @ q={q_max}"
    if signal.signal_type in {"LARGE_TAKER_TRADE", "LARGE_NEW_WALLET_TRADE"}:
        title = f"[TRADE] ${payload.get('notional_usd')} {payload.get('market_title')}"
    return title


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
