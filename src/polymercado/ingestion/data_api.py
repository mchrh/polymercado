from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.ingestion.http import fetch_json
from polymercado.ingestion.universe import select_tracked_markets
from polymercado.models import (
    MarketMetricsTS,
    SignalEvent,
    SignalType,
    Trade,
    TradeSide,
    Wallet,
)
from polymercado.signals.wallets import (
    build_trade_payload,
    is_dormant,
    is_new_wallet,
    severity_for_trade,
)
from polymercado.trades import compute_notional, parse_trade_ts, trade_dedupe_key
from polymercado.utils import to_decimal, utc_now

DATA_BASE = "https://data-api.polymarket.com"


def _dialect_insert(session: Session):
    if session.bind and session.bind.dialect.name == "sqlite":
        return sqlite_insert
    return pg_insert


def _latest_trade_ts(session: Session) -> datetime | None:
    return session.execute(select(func.max(Trade.trade_ts))).scalar_one_or_none()


def _latest_market_metrics(
    session: Session, condition_id: str
) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(MarketMetricsTS)
            .where(MarketMetricsTS.condition_id == condition_id)
            .order_by(MarketMetricsTS.ts.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if not row:
        return None
    return {
        "market_liquidity": float(row.gamma_liquidity) if row.gamma_liquidity else None,
        "market_volume": float(row.gamma_volume) if row.gamma_volume else None,
        "market_open_interest": float(row.open_interest) if row.open_interest else None,
    }


def sync_open_interest(session: Session, settings: AppSettings) -> int:
    condition_ids = select_tracked_markets(session, settings)
    if not condition_ids:
        return 0

    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0
    try:
        for i in range(0, len(condition_ids), 50):
            batch = condition_ids[i : i + 50]
            params = {"market": batch}
            data = fetch_json(client, f"{DATA_BASE}/oi", params=params)
            for item in data:
                condition_id = item.get("market")
                if not condition_id:
                    continue
                snapshot = MarketMetricsTS(
                    condition_id=condition_id,
                    ts=utc_now(),
                    open_interest=item.get("value"),
                )
                session.add(snapshot)
                processed += 1
    finally:
        client.close()

    session.commit()
    return processed


def sync_large_trades(session: Session, settings: AppSettings) -> int:
    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0
    inserted = 0

    try:
        last_trade_ts = _latest_trade_ts(session)
        stop_ts = None
        if last_trade_ts:
            stop_ts = last_trade_ts - timedelta(
                seconds=settings.TRADE_SAFETY_WINDOW_SECONDS
            )

        offset = 0
        while True:
            params = {
                "limit": settings.TRADES_PAGE_LIMIT,
                "offset": offset,
                "takerOnly": str(settings.TAKER_ONLY).lower(),
                "filterType": "CASH",
                "filterAmount": settings.LARGE_TRADE_USD_THRESHOLD,
            }
            trades = fetch_json(client, f"{DATA_BASE}/trades", params=params)
            if not trades:
                break

            for trade in trades:
                trade_ts = parse_trade_ts(trade.get("timestamp"))
                if trade_ts is None:
                    continue
                if stop_ts and trade_ts < stop_ts:
                    return inserted

                dedupe = trade_dedupe_key(trade)
                price = to_decimal(trade.get("price"))
                size = to_decimal(trade.get("size"))
                notional = compute_notional(price, size)
                if notional is None:
                    continue

                side = trade.get("side")
                if side not in (TradeSide.BUY.value, TradeSide.SELL.value):
                    continue
                if not trade.get("conditionId") or not trade.get("asset"):
                    continue

                trade_row = {
                    "trade_pk": dedupe,
                    "transaction_hash": trade.get("transactionHash"),
                    "wallet": trade.get("proxyWallet"),
                    "condition_id": trade.get("conditionId"),
                    "token_id": trade.get("asset"),
                    "side": TradeSide(side),
                    "price": price,
                    "size": size,
                    "notional_usd": notional,
                    "trade_ts": trade_ts,
                    "raw": trade,
                }

                insert_stmt = _dialect_insert(session)(Trade).values(**trade_row)
                stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=[Trade.trade_pk]
                )
                result = session.execute(stmt)
                processed += 1

                if result.rowcount and result.rowcount > 0:
                    inserted += 1
                    _update_wallets_and_signals(
                        session, trade, notional, trade_ts, settings
                    )

            if len(trades) < settings.TRADES_PAGE_LIMIT:
                break
            offset += settings.TRADES_PAGE_LIMIT
    finally:
        client.close()

    session.commit()
    return inserted


def _update_wallets_and_signals(
    session: Session,
    trade: dict[str, Any],
    notional: Decimal,
    trade_ts: datetime,
    settings: AppSettings,
) -> None:
    wallet_address = trade.get("proxyWallet")
    wallet = None
    wallet_was_dormant = False
    if wallet_address:
        wallet = session.get(Wallet, wallet_address)

        if wallet is None:
            wallet = Wallet(
                wallet=wallet_address,
                first_seen_at=utc_now(),
                last_seen_at=utc_now(),
                first_trade_ts=trade_ts,
                lifetime_notional_usd=notional,
            )
            session.add(wallet)
        else:
            wallet_was_dormant = is_dormant(wallet, trade_ts, settings)
            wallet.last_seen_at = utc_now()
            wallet.lifetime_notional_usd = (
                wallet.lifetime_notional_usd + notional
                if wallet.lifetime_notional_usd is not None
                else notional
            )

    market_metrics = _latest_market_metrics(session, trade.get("conditionId"))
    low_liquidity = False
    if market_metrics and market_metrics.get("market_liquidity") is not None:
        low_liquidity = (
            market_metrics["market_liquidity"] < settings.MIN_GAMMA_LIQUIDITY
        )

    is_new = wallet is not None and is_new_wallet(wallet, trade_ts, settings)
    severity = severity_for_trade(notional, is_new=is_new, low_liquidity=low_liquidity)

    payload = build_trade_payload(
        trade,
        wallet,
        notional,
        trade_ts,
        market_metrics,
        settings.config_snapshot(
            [
                "LARGE_TRADE_USD_THRESHOLD",
                "NEW_WALLET_WINDOW_DAYS",
                "DORMANT_WINDOW_DAYS",
            ]
        ),
    )

    _emit_signal(session, SignalType.LARGE_TAKER_TRADE, trade, payload, severity)

    if is_new:
        _emit_signal(
            session, SignalType.LARGE_NEW_WALLET_TRADE, trade, payload, severity
        )

    if wallet is not None and wallet_was_dormant:
        _emit_signal(
            session, SignalType.DORMANT_WALLET_REACTIVATION, trade, payload, severity
        )


def _emit_signal(
    session: Session,
    signal_type: SignalType,
    trade: dict[str, Any],
    payload: dict[str, Any],
    severity: int,
) -> None:
    dedupe_key = f"{signal_type}:{trade_dedupe_key(trade)}"

    insert_stmt = _dialect_insert(session)(SignalEvent).values(
        signal_type=signal_type,
        dedupe_key=dedupe_key,
        created_at=utc_now(),
        severity=severity,
        wallet=trade.get("proxyWallet"),
        condition_id=trade.get("conditionId"),
        payload=payload,
    )
    stmt = insert_stmt.on_conflict_do_nothing(index_elements=[SignalEvent.dedupe_key])
    session.execute(stmt)
