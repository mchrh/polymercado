from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, func, select
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
    WalletMarketExposure,
)
from polymercado.signals.wallets import (
    build_trade_payload,
    is_dormant,
    is_new_wallet,
    severity_for_trade,
)
from polymercado.trades import compute_notional, parse_trade_ts, trade_dedupe_key
from polymercado.utils import ensure_utc, to_decimal, utc_now
from polymercado.utils import safe_lower

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


def sync_wallet_positions(session: Session, settings: AppSettings) -> int:
    if not settings.WALLET_POSITIONS_ENABLED:
        return 0

    now = utc_now()
    wallets = (
        session.execute(
            select(Wallet).where(
                Wallet.tracked_until.is_not(None), Wallet.tracked_until >= now
            )
        )
        .scalars()
        .all()
    )
    if not wallets:
        return 0

    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0
    try:
        for wallet in wallets:
            params = {
                "user": wallet.wallet,
                "limit": settings.POSITIONS_PAGE_LIMIT,
                "offset": 0,
                "sizeThreshold": settings.POSITIONS_SIZE_THRESHOLD,
            }
            positions = fetch_json(client, f"{DATA_BASE}/positions", params=params)
            _upsert_wallet_positions(session, wallet.wallet, positions)
            processed += len(positions)
    finally:
        client.close()

    session.commit()
    return processed


def _upsert_wallet_positions(
    session: Session, wallet: str, positions: list[dict[str, Any]]
) -> None:
    aggregates: dict[str, dict[str, Decimal]] = {}
    for position in positions:
        condition_id = position.get("conditionId")
        size = to_decimal(position.get("size"))
        if not condition_id or size is None:
            continue
        avg_price = to_decimal(position.get("avgPrice"))
        outcome = safe_lower(position.get("outcome"))
        sign = Decimal("-1") if outcome == "no" else Decimal("1")

        bucket = aggregates.setdefault(
            condition_id,
            {
                "net": Decimal("0"),
                "cost": Decimal("0"),
                "total": Decimal("0"),
            },
        )
        bucket["net"] += size * sign
        if avg_price is not None:
            bucket["cost"] += abs(size) * avg_price
        bucket["total"] += abs(size)

    now = utc_now()
    active_conditions = set(aggregates.keys())
    if active_conditions:
        session.execute(
            delete(WalletMarketExposure).where(
                WalletMarketExposure.wallet == wallet,
                ~WalletMarketExposure.condition_id.in_(active_conditions),
            )
        )
    else:
        session.execute(
            delete(WalletMarketExposure).where(WalletMarketExposure.wallet == wallet)
        )

    for condition_id, bucket in aggregates.items():
        total = bucket["total"]
        avg_entry = bucket["cost"] / total if total > 0 else None
        insert_stmt = _dialect_insert(session)(WalletMarketExposure).values(
            wallet=wallet,
            condition_id=condition_id,
            net_shares=bucket["net"],
            avg_entry_price=avg_entry,
            last_updated_at=now,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[
                WalletMarketExposure.wallet,
                WalletMarketExposure.condition_id,
            ],
            set_={
                "net_shares": insert_stmt.excluded.net_shares,
                "avg_entry_price": insert_stmt.excluded.avg_entry_price,
                "last_updated_at": insert_stmt.excluded.last_updated_at,
            },
        )
        session.execute(stmt)


def sync_large_trades(session: Session, settings: AppSettings) -> int:
    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0
    inserted = 0

    wallet_cache: dict[str, Wallet] = {}

    try:
        last_trade_ts = ensure_utc(_latest_trade_ts(session))
        stop_ts = None
        if last_trade_ts:
            stop_ts = last_trade_ts - timedelta(
                seconds=settings.TRADE_SAFETY_WINDOW_SECONDS
            )
        else:
            stop_ts = utc_now() - timedelta(
                hours=settings.TRADES_INITIAL_LOOKBACK_HOURS
            )
        stop_ts = ensure_utc(stop_ts)

        offset = 0
        pages = 0
        while True:
            if pages >= settings.TRADES_MAX_PAGES:
                break
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

            stop_reached = False
            for trade in trades:
                trade_ts = parse_trade_ts(trade.get("timestamp"))
                if trade_ts is None:
                    continue
                if stop_ts and trade_ts < stop_ts:
                    stop_reached = True
                    break

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
                        session,
                        trade,
                        notional,
                        trade_ts,
                        settings,
                        wallet_cache,
                    )

            session.commit()
            session.expire_all()
            pages += 1

            if stop_reached:
                break
            if len(trades) < settings.TRADES_PAGE_LIMIT:
                break
            offset += settings.TRADES_PAGE_LIMIT
    finally:
        client.close()

    return inserted


def _update_wallets_and_signals(
    session: Session,
    trade: dict[str, Any],
    notional: Decimal,
    trade_ts: datetime,
    settings: AppSettings,
    wallet_cache: dict[str, Wallet],
) -> None:
    wallet_address = trade.get("proxyWallet")
    wallet = None
    wallet_was_dormant = False
    track_until = None
    if settings.TRACK_WALLET_DAYS_AFTER_LARGE_TRADE > 0:
        track_until = utc_now() + timedelta(
            days=settings.TRACK_WALLET_DAYS_AFTER_LARGE_TRADE
        )
    if wallet_address:
        wallet = wallet_cache.get(wallet_address)
        if wallet is None:
            wallet = session.get(Wallet, wallet_address)
            if wallet is not None:
                wallet_cache[wallet_address] = wallet

        if wallet is None:
            wallet = Wallet(
                wallet=wallet_address,
                first_seen_at=utc_now(),
                last_seen_at=utc_now(),
                first_trade_ts=trade_ts,
                tracked_until=track_until,
                lifetime_notional_usd=notional,
            )
            session.add(wallet)
            wallet_cache[wallet_address] = wallet
        else:
            wallet_was_dormant = is_dormant(wallet, trade_ts, settings)
            wallet.last_seen_at = utc_now()
            wallet.lifetime_notional_usd = (
                wallet.lifetime_notional_usd + notional
                if wallet.lifetime_notional_usd is not None
                else notional
            )
            desired_tracked_until = ensure_utc(track_until)
            current_tracked_until = ensure_utc(wallet.tracked_until)
            if desired_tracked_until and (
                current_tracked_until is None
                or current_tracked_until < desired_tracked_until
            ):
                wallet.tracked_until = desired_tracked_until

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
