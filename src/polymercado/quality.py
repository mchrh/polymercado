from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.ingestion.universe import select_tracked_markets
from polymercado.markets import resolve_binary_tokens
from polymercado.models import (
    DataQualityIssue,
    Market,
    OrderbookLevels,
    OrderbookSide,
    Trade,
    Wallet,
)
from polymercado.utils import to_decimal, utc_now


def run_data_quality_checks(session: Session, settings: AppSettings) -> int:
    if not settings.DATA_QUALITY_ENABLED:
        return 0

    issues: list[DataQualityIssue] = []
    now = utc_now()

    tracked_ids = select_tracked_markets(session, settings)
    if tracked_ids:
        markets = (
            session.execute(select(Market).where(Market.condition_id.in_(tracked_ids)))
            .scalars()
            .all()
        )
        missing_tokens: list[str] = []
        token_to_condition: dict[str, str] = {}
        token_ids: list[str] = []

        for market in markets:
            yes_token, no_token = resolve_binary_tokens(
                market.token_ids, market.outcomes
            )
            if not yes_token or not no_token:
                missing_tokens.append(market.condition_id)
                continue
            token_to_condition[yes_token] = market.condition_id
            token_to_condition[no_token] = market.condition_id
            token_ids.extend([yes_token, no_token])

        if missing_tokens:
            sample = ", ".join(missing_tokens[:5])
            issues.append(
                DataQualityIssue(
                    check_name="missing_token_ids",
                    severity=3,
                    message=(
                        f"{len(missing_tokens)} tracked markets missing token ids. "
                        f"Sample: {sample}"
                    ),
                    created_at=now,
                )
            )

        if token_ids:
            rows = session.execute(
                select(OrderbookLevels.token_id, OrderbookLevels.side).where(
                    OrderbookLevels.token_id.in_(token_ids)
                )
            ).all()
            present = {(row.token_id, row.side) for row in rows}
            missing_books: list[str] = []
            for token_id in token_ids:
                if (token_id, OrderbookSide.BID) not in present or (
                    token_id,
                    OrderbookSide.ASK,
                ) not in present:
                    condition_id = token_to_condition.get(token_id, "unknown")
                    missing_books.append(f"{condition_id}:{token_id}")

            if missing_books:
                sample = ", ".join(missing_books[:5])
                issues.append(
                    DataQualityIssue(
                        check_name="missing_orderbooks",
                        severity=3,
                        message=(
                            f"{len(missing_books)} tracked tokens missing book sides. "
                            f"Sample: {sample}"
                        ),
                        created_at=now,
                    )
                )

            out_of_bounds: list[str] = []
            books = (
                session.execute(
                    select(OrderbookLevels).where(
                        OrderbookLevels.token_id.in_(token_ids)
                    )
                )
                .scalars()
                .all()
            )
            for book in books:
                levels = book.levels or []
                for level in levels:
                    price = (
                        to_decimal(level.get("price"))
                        if isinstance(level, dict)
                        else None
                    )
                    if price is None:
                        continue
                    if price < 0 or price > 1:
                        out_of_bounds.append(f"{book.token_id}:{price}")
                        break

            if out_of_bounds:
                sample = ", ".join(out_of_bounds[:5])
                issues.append(
                    DataQualityIssue(
                        check_name="orderbook_price_bounds",
                        severity=4,
                        message=(
                            f"{len(out_of_bounds)} orderbook levels outside [0,1]. "
                            f"Sample: {sample}"
                        ),
                        created_at=now,
                    )
                )

    trade_limit = settings.DATA_QUALITY_TRADE_SAMPLE_LIMIT
    trades = (
        session.execute(
            select(Trade).order_by(Trade.trade_ts.desc()).limit(trade_limit)
        )
        .scalars()
        .all()
    )
    mismatches = 0
    for trade in trades:
        price = to_decimal(trade.price)
        size = to_decimal(trade.size)
        notional = to_decimal(trade.notional_usd)
        if price is None or size is None or notional is None:
            continue
        recomputed = price * size
        if abs(recomputed - notional) > Decimal("0.01"):
            mismatches += 1

    if mismatches:
        issues.append(
            DataQualityIssue(
                check_name="trade_notional_mismatch",
                severity=2,
                message=f"{mismatches} trades have notional mismatch > $0.01.",
                created_at=now,
            )
        )

    cutoff = now - timedelta(hours=1)
    new_wallets = session.execute(
        select(func.count()).select_from(Wallet).where(Wallet.first_seen_at >= cutoff)
    ).scalar_one()
    if new_wallets > settings.DATA_QUALITY_MAX_NEW_WALLETS_PER_HOUR:
        issues.append(
            DataQualityIssue(
                check_name="new_wallet_rate",
                severity=2,
                message=(
                    f"{new_wallets} wallets first seen in the last hour; "
                    f"threshold {settings.DATA_QUALITY_MAX_NEW_WALLETS_PER_HOUR}."
                ),
                created_at=now,
            )
        )

    if issues:
        session.add_all(issues)
        session.commit()
    return len(issues)
