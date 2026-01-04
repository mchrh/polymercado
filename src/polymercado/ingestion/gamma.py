from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import update
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.ingestion.http import fetch_json
from polymercado.models import Market, MarketMetricsTS, SignalEvent, SignalType, Tag
from polymercado.utils import parse_datetime, parse_jsonish_array, to_decimal, utc_now

GAMMA_BASE = "https://gamma-api.polymarket.com"


def _dialect_insert(session: Session):
    if session.bind and session.bind.dialect.name == "sqlite":
        return sqlite_insert
    return pg_insert


def _parse_tag_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_tag_metadata(session: Session, settings: AppSettings) -> int:
    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0
    try:
        processed += _sync_tags(client, session, settings)
        sport_tag_ids = _sync_sports(client)
        if sport_tag_ids is not None:
            session.execute(update(Tag).values(is_sport=False))
            if sport_tag_ids:
                session.execute(
                    update(Tag)
                    .where(Tag.id.in_(sorted(sport_tag_ids)))
                    .values(is_sport=True)
                )
    finally:
        client.close()

    session.commit()
    return processed


def _sync_tags(client: httpx.Client, session: Session, settings: AppSettings) -> int:
    processed = 0
    offset = 0
    limit = settings.TAGS_PAGE_LIMIT
    for _ in range(settings.TAGS_MAX_PAGES):
        params = {"limit": limit, "offset": offset}
        tags = fetch_json(client, f"{GAMMA_BASE}/tags", params=params)
        if not tags:
            break
        for tag in tags:
            tag_id = _parse_tag_id(tag.get("id"))
            if tag_id is None:
                continue
            insert_stmt = _dialect_insert(session)(Tag).values(
                id=tag_id,
                label=tag.get("label"),
                slug=tag.get("slug"),
            )
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[Tag.id],
                set_={
                    "label": insert_stmt.excluded.label,
                    "slug": insert_stmt.excluded.slug,
                },
            )
            session.execute(stmt)
            processed += 1
        if len(tags) < limit:
            break
        offset += limit
    return processed


def _sync_sports(client: httpx.Client) -> set[int] | None:
    try:
        sports = fetch_json(client, f"{GAMMA_BASE}/sports")
    except httpx.HTTPError:
        return None
    if not sports:
        return set()
    tag_ids: set[int] = set()
    for sport in sports:
        raw = sport.get("tags") or ""
        if not isinstance(raw, str):
            continue
        for part in raw.split(","):
            tag_id = _parse_tag_id(part.strip())
            if tag_id is not None:
                tag_ids.add(tag_id)
    return tag_ids


def parse_market(market: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    token_ids = parse_jsonish_array(market.get("clobTokenIds"))
    outcomes = parse_jsonish_array(market.get("outcomes"))

    volume = to_decimal(market.get("volumeNum") or market.get("volume"))
    liquidity = to_decimal(market.get("liquidityNum") or market.get("liquidity"))

    tag_ids = []
    for tag in event.get("tags") or []:
        tag_id = tag.get("id")
        if tag_id is not None:
            try:
                tag_ids.append(int(tag_id))
            except ValueError:
                continue

    neg_risk = market.get("negRisk")
    if neg_risk is None:
        neg_risk = event.get("negRisk")

    active = market.get("active")
    if active is None:
        active = event.get("active")
    closed = market.get("closed")
    if closed is None:
        closed = event.get("closed")

    return {
        "condition_id": market.get("conditionId"),
        "market_id": market.get("id"),
        "event_id": event.get("id"),
        "slug": market.get("slug"),
        "question": market.get("question"),
        "title": market.get("question") or event.get("title"),
        "active": active,
        "closed": closed,
        "tag_ids": tag_ids or None,
        "neg_risk": neg_risk,
        "outcomes": outcomes or None,
        "token_ids": token_ids or None,
        "start_time": parse_datetime(market.get("startDate") or event.get("startDate")),
        "end_time": parse_datetime(market.get("endDate") or event.get("endDate")),
        "created_at": parse_datetime(market.get("createdAt") or event.get("createdAt")),
        "updated_at": parse_datetime(market.get("updatedAt") or event.get("updatedAt")),
        "last_seen_at": utc_now(),
        "gamma_volume": float(volume) if volume is not None else None,
        "gamma_liquidity": float(liquidity) if liquidity is not None else None,
    }


def upsert_market(session: Session, values: dict[str, Any]) -> None:
    market_columns = set(Market.__table__.columns.keys())
    insert_stmt = _dialect_insert(session)(Market).values(
        **{k: v for k, v in values.items() if k in market_columns}
    )
    update_values = {
        key: getattr(insert_stmt.excluded, key)
        for key in values
        if key
        in {
            "market_id",
            "event_id",
            "slug",
            "question",
            "title",
            "active",
            "closed",
            "tag_ids",
            "neg_risk",
            "outcomes",
            "token_ids",
            "start_time",
            "end_time",
            "updated_at",
            "last_seen_at",
        }
    }
    update_values["last_seen_at"] = insert_stmt.excluded.last_seen_at

    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[Market.condition_id], set_=update_values
    )
    session.execute(stmt)


def insert_metric_snapshot(
    session: Session, condition_id: str, volume: float | None, liquidity: float | None
) -> None:
    if volume is None and liquidity is None:
        return
    snapshot = MarketMetricsTS(
        condition_id=condition_id,
        ts=utc_now(),
        gamma_volume=volume,
        gamma_liquidity=liquidity,
    )
    session.add(snapshot)


def emit_new_market_signal(session: Session, values: dict[str, Any]) -> None:
    payload = {
        "condition_id": values["condition_id"],
        "slug": values.get("slug"),
        "title": values.get("title"),
        "tags": values.get("tag_ids"),
        "start_time": values.get("start_time").isoformat()
        if values.get("start_time")
        else None,
        "end_time": values.get("end_time").isoformat()
        if values.get("end_time")
        else None,
        "token_ids": values.get("token_ids"),
    }
    dedupe_key = f"NEW_MARKET:{values['condition_id']}"

    insert_stmt = _dialect_insert(session)(SignalEvent).values(
        signal_type=SignalType.NEW_MARKET,
        dedupe_key=dedupe_key,
        created_at=utc_now(),
        severity=1,
        condition_id=values["condition_id"],
        payload=payload,
    )
    stmt = insert_stmt.on_conflict_do_nothing(index_elements=[SignalEvent.dedupe_key])
    session.execute(stmt)


def sync_gamma_events(session: Session, settings: AppSettings) -> int:
    client = httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
    processed = 0

    try:
        offset = 0
        for _ in range(settings.GAMMA_EVENTS_MAX_PAGES):
            params = {
                "active": "true",
                "closed": "false",
                "limit": settings.GAMMA_EVENTS_PAGE_LIMIT,
                "offset": offset,
                "order": "id",
                "ascending": "false",
            }
            events = fetch_json(client, f"{GAMMA_BASE}/events", params=params)
            if not events:
                break
            for event in events:
                for market in event.get("markets") or []:
                    values = parse_market(market, event)
                    condition_id = values.get("condition_id")
                    if not condition_id:
                        continue

                    existing = session.get(Market, condition_id)
                    if existing is None:
                        emit_new_market_signal(session, values)

                    upsert_market(session, values)
                    insert_metric_snapshot(
                        session,
                        condition_id,
                        values.get("gamma_volume"),
                        values.get("gamma_liquidity"),
                    )
                    processed += 1
            offset += settings.GAMMA_EVENTS_PAGE_LIMIT
    finally:
        client.close()

    session.commit()
    return processed
