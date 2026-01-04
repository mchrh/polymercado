from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from polymercado.config import AppSettings
from polymercado.models import Wallet


def is_new_wallet(wallet: Wallet, trade_ts: datetime, settings: AppSettings) -> bool:
    window = timedelta(days=settings.NEW_WALLET_WINDOW_DAYS)
    return trade_ts <= wallet.first_seen_at + window


def is_dormant(wallet: Wallet, trade_ts: datetime, settings: AppSettings) -> bool:
    window = timedelta(days=settings.DORMANT_WINDOW_DAYS)
    last_seen = wallet.last_seen_at
    return last_seen is not None and trade_ts >= last_seen + window


def severity_for_trade(notional: Decimal, is_new: bool, low_liquidity: bool) -> int:
    value = float(notional)
    if value >= 1_000_000:
        severity = 5
    elif value >= 250_000:
        severity = 4
    elif value >= 50_000:
        severity = 3
    else:
        severity = 2

    if is_new:
        severity += 1
    if low_liquidity:
        severity += 1
    return min(severity, 5)


def build_trade_payload(
    trade: dict[str, Any],
    wallet: Wallet | None,
    notional: Decimal,
    trade_ts: datetime,
    market_metrics: dict[str, Any] | None,
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "wallet": trade.get("proxyWallet"),
        "trade_ts": trade_ts.isoformat(),
        "condition_id": trade.get("conditionId"),
        "token_id": trade.get("asset"),
        "side": trade.get("side"),
        "size_shares": trade.get("size"),
        "price": trade.get("price"),
        "notional_usd": float(notional),
        "market_slug": trade.get("slug"),
        "market_title": trade.get("title"),
        "event_slug": trade.get("eventSlug"),
        "outcome": trade.get("outcome"),
        "tx_hash": trade.get("transactionHash"),
        "config_snapshot": config_snapshot,
    }
    if wallet:
        payload["wallet_first_seen_at"] = wallet.first_seen_at.isoformat()
        payload["wallet_age_days"] = (
            (wallet.last_seen_at - wallet.first_seen_at).days
            if wallet.last_seen_at and wallet.first_seen_at
            else None
        )
    if market_metrics:
        payload.update(market_metrics)
    return payload
