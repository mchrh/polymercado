from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.markets import resolve_binary_tokens
from polymercado.models import (
    Market,
    OrderbookLevels,
    OrderbookSide,
    SignalEvent,
    SignalType,
)
from polymercado.signals.arb import compute_arb, fill_levels, normalize_levels
from polymercado.utils import utc_now


def _dialect_insert(session: Session):
    if session.bind and session.bind.dialect.name == "sqlite":
        return sqlite_insert
    return pg_insert


def _severity(edge: Decimal, q_max: Decimal) -> int:
    edge_pct = float(edge)
    if edge_pct >= 0.015 and q_max >= Decimal("500"):
        return 4
    if edge_pct >= 0.01 and q_max >= Decimal("100"):
        return 3
    return 2


def run_signal_engine(session: Session, settings: AppSettings) -> int:
    now = utc_now()
    processed = 0

    markets = session.execute(select(Market)).scalars().all()
    for market in markets:
        yes_token, no_token = resolve_binary_tokens(market.token_ids, market.outcomes)
        if not yes_token or not no_token:
            continue

        yes_asks = session.get(
            OrderbookLevels, {"token_id": yes_token, "side": OrderbookSide.ASK}
        )
        no_asks = session.get(
            OrderbookLevels, {"token_id": no_token, "side": OrderbookSide.ASK}
        )
        if not yes_asks or not no_asks:
            continue
        if yes_asks.as_of and now - yes_asks.as_of > timedelta(
            seconds=settings.ARB_MAX_BOOK_AGE_SECONDS
        ):
            continue
        if no_asks.as_of and now - no_asks.as_of > timedelta(
            seconds=settings.ARB_MAX_BOOK_AGE_SECONDS
        ):
            continue

        asks_yes = normalize_levels(yes_asks.levels)
        asks_no = normalize_levels(no_asks.levels)
        if not asks_yes or not asks_no:
            continue

        result = compute_arb(asks_yes, asks_no, settings)
        q_max = result["q_max"]
        edge_at_q_max = result["edge_at_q_max"]
        edge_at_min = result["edge_at_min_q"]
        if not q_max or not edge_at_q_max:
            continue

        if q_max < Decimal(str(settings.ARB_MIN_EXECUTABLE_SHARES)):
            continue

        cooldown_window = now - timedelta(seconds=settings.ARB_MARKET_COOLDOWN_SECONDS)
        recent = (
            session.execute(
                select(SignalEvent)
                .where(
                    SignalEvent.signal_type == SignalType.ARB_BUY_BOTH,
                    SignalEvent.condition_id == market.condition_id,
                    SignalEvent.created_at >= cooldown_window,
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if recent:
            continue

        best_ask_yes = asks_yes[0].price
        best_ask_no = asks_no[0].price
        top_of_book_sum = best_ask_yes + best_ask_no

        payload = {
            "condition_id": market.condition_id,
            "yes_token_id": yes_token,
            "no_token_id": no_token,
            "neg_risk": market.neg_risk,
            "as_of_yes": yes_asks.as_of.isoformat() if yes_asks.as_of else None,
            "as_of_no": no_asks.as_of.isoformat() if no_asks.as_of else None,
            "best_ask_yes": str(best_ask_yes),
            "best_ask_no": str(best_ask_no),
            "top_of_book_sum": str(top_of_book_sum),
            "edge_min": settings.ARB_EDGE_MIN,
            "min_executable_shares": settings.ARB_MIN_EXECUTABLE_SHARES,
            "q_max": str(q_max),
            "edge_at_min_q": str(edge_at_min) if edge_at_min is not None else None,
            "edge_at_q_max": str(edge_at_q_max),
            "avg_ask_yes_at_q_max": str(result["avg_ask_yes_at_q_max"]),
            "avg_ask_no_at_q_max": str(result["avg_ask_no_at_q_max"]),
            "asks_yes_levels": fill_levels(asks_yes, q_max),
            "asks_no_levels": fill_levels(asks_no, q_max),
            "config_snapshot": settings.config_snapshot(
                [
                    "ARB_EDGE_MIN",
                    "ARB_MIN_EXECUTABLE_SHARES",
                    "ARB_MAX_SHARES_TO_EVALUATE",
                    "ARB_MAX_BOOK_AGE_SECONDS",
                    "TAKER_FEE_BPS",
                ]
            ),
        }

        dedupe_key = f"ARB_BUY_BOTH:{market.condition_id}:{float(edge_at_q_max):.4f}:{float(q_max):.2f}"
        severity = _severity(edge_at_q_max, q_max)

        insert_stmt = _dialect_insert(session)(SignalEvent).values(
            signal_type=SignalType.ARB_BUY_BOTH,
            dedupe_key=dedupe_key,
            created_at=now,
            severity=severity,
            condition_id=market.condition_id,
            payload=payload,
        )
        stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=[SignalEvent.dedupe_key]
        )
        session.execute(stmt)
        processed += 1

    session.commit()
    return processed
