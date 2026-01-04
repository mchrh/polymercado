from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.ingestion.universe import select_tracked_markets
from polymercado.markets import resolve_binary_tokens
from polymercado.models import Market, MarketMetricsTS, OrderbookLevels, OrderbookSide
from polymercado.utils import parse_datetime, utc_now

CLOB_BASE = "https://clob.polymarket.com"


def _dialect_insert(session: Session):
    if session.bind and session.bind.dialect.name == "sqlite":
        return sqlite_insert
    return pg_insert


def fetch_books(client: httpx.Client, token_ids: list[str]) -> list[dict[str, Any]]:
    if not token_ids:
        return []
    payload = [{"token_id": token_id} for token_id in token_ids]
    response = client.post(f"{CLOB_BASE}/books", json=payload)
    response.raise_for_status()
    return response.json()


def upsert_orderbook(session: Session, book: dict[str, Any]) -> None:
    condition_id = book.get("market")
    token_id = book.get("asset_id")
    if not condition_id or not token_id:
        return

    as_of = parse_datetime(book.get("timestamp"))
    for side, levels in (
        (OrderbookSide.BID, book.get("bids")),
        (OrderbookSide.ASK, book.get("asks")),
    ):
        if levels is None:
            continue
        insert_stmt = _dialect_insert(session)(OrderbookLevels).values(
            token_id=token_id,
            side=side,
            condition_id=condition_id,
            levels=levels,
            tick_size=book.get("tick_size"),
            min_order_size=book.get("min_order_size"),
            neg_risk=book.get("neg_risk"),
            as_of=as_of,
            hash=book.get("hash"),
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[OrderbookLevels.token_id, OrderbookLevels.side],
            set_={
                "condition_id": insert_stmt.excluded.condition_id,
                "levels": insert_stmt.excluded.levels,
                "tick_size": insert_stmt.excluded.tick_size,
                "min_order_size": insert_stmt.excluded.min_order_size,
                "neg_risk": insert_stmt.excluded.neg_risk,
                "as_of": insert_stmt.excluded.as_of,
                "hash": insert_stmt.excluded.hash,
            },
        )
        session.execute(stmt)


def sync_orderbooks(session: Session, settings: AppSettings) -> int:
    condition_ids = select_tracked_markets(session, settings)
    if not condition_ids:
        return 0

    markets = session.query(Market).filter(Market.condition_id.in_(condition_ids)).all()

    token_map: dict[str, tuple[str | None, str | None]] = {}
    all_tokens: list[str] = []
    for market in markets:
        token_ids = market.token_ids or []
        for token_id in token_ids:
            all_tokens.append(token_id)
        token_map[market.condition_id] = resolve_binary_tokens(
            token_ids, market.outcomes
        )

    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0
    try:
        for i in range(0, len(all_tokens), 500):
            batch = all_tokens[i : i + 500]
            books = fetch_books(client, batch)
            for book in books:
                upsert_orderbook(session, book)
                processed += 1

            _emit_metric_snapshot(session, books, token_map)
    finally:
        client.close()

    session.commit()
    return processed


def _emit_metric_snapshot(
    session: Session,
    books: list[dict[str, Any]],
    token_map: dict[str, tuple[str | None, str | None]],
) -> None:
    if not books:
        return

    best_prices: dict[str, dict[str, float | None]] = defaultdict(dict)
    for book in books:
        condition_id = book.get("market")
        token_id = book.get("asset_id")
        if not condition_id or not token_id:
            continue
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None
        best_prices[token_id] = {"bid": best_bid, "ask": best_ask}

    for condition_id, (yes_token, no_token) in token_map.items():
        if not yes_token or not no_token:
            continue
        yes_prices = best_prices.get(yes_token, {})
        no_prices = best_prices.get(no_token, {})
        snapshot = MarketMetricsTS(
            condition_id=condition_id,
            ts=utc_now(),
            best_bid_yes=yes_prices.get("bid"),
            best_ask_yes=yes_prices.get("ask"),
            best_bid_no=no_prices.get("bid"),
            best_ask_no=no_prices.get("ask"),
        )
        if snapshot.best_bid_yes is not None and snapshot.best_ask_yes is not None:
            snapshot.spread_yes = snapshot.best_ask_yes - snapshot.best_bid_yes
        if snapshot.best_bid_no is not None and snapshot.best_ask_no is not None:
            snapshot.spread_no = snapshot.best_ask_no - snapshot.best_bid_no
        session.add(snapshot)
