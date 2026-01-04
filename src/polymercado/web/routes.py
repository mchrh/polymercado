from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from polymercado.alerts.dispatcher import build_notification_key
from polymercado.config import AppSettings, load_settings
from polymercado.markets import compute_market_score, resolve_binary_tokens
from polymercado.models import (
    AlertAck,
    AlertLog,
    AlertRule,
    AppConfig,
    DataQualityIssue,
    JobRun,
    Market,
    MarketMetricsTS,
    OrderbookLevels,
    OrderbookSide,
    SignalEvent,
    SignalType,
    Trade,
    TrackedMarket,
    Wallet,
    WalletMarketExposure,
)
from polymercado.signals.arb import avg_ask, normalize_levels
from polymercado.utils import to_decimal, utc_now

router = APIRouter()


def _session_from_request(request: Request) -> Session:
    session_factory = request.app.state.session_factory
    return session_factory()


def _settings_from_request(request: Request) -> AppSettings:
    return request.app.state.settings


def _latest_metrics_subquery():
    return (
        select(
            MarketMetricsTS.condition_id,
            func.max(MarketMetricsTS.ts).label("max_ts"),
        )
        .group_by(MarketMetricsTS.condition_id)
        .subquery()
    )


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    items = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            items.append(int(raw))
        except ValueError:
            continue
    return items


def _depth_within_cents(
    levels: list[dict[str, Any]] | None, cents: float
) -> float | None:
    if not levels:
        return None
    best_price = to_decimal(levels[0].get("price"))
    if best_price is None:
        return None
    threshold = best_price + Decimal(str(cents))
    total = Decimal("0")
    for level in levels:
        price = to_decimal(level.get("price")) if isinstance(level, dict) else None
        size = to_decimal(level.get("size")) if isinstance(level, dict) else None
        if price is None or size is None:
            continue
        if price <= threshold:
            total += size
        else:
            break
    return float(total)


def _top_wallets(
    session: Session, condition_id: str, since: timedelta
) -> list[tuple[str, float]]:
    cutoff = utc_now() - since
    rows = session.execute(
        select(Trade.wallet, func.sum(Trade.notional_usd).label("notional"))
        .where(
            Trade.condition_id == condition_id,
            Trade.wallet.is_not(None),
            Trade.trade_ts >= cutoff,
        )
        .group_by(Trade.wallet)
        .order_by(desc("notional"))
        .limit(10)
    ).all()
    return [(row.wallet, float(row.notional or 0)) for row in rows]


@router.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse(url="/markets")


@router.get("/markets", response_class=HTMLResponse)
def markets(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        settings = _settings_from_request(request)
        params = request.query_params
        include_tags = _parse_int_list(params.get("include_tags"))
        exclude_tags = _parse_int_list(params.get("exclude_tags"))
        min_volume = _parse_float(params.get("min_volume"))
        min_liquidity = _parse_float(params.get("min_liquidity"))
        min_oi = _parse_float(params.get("min_oi"))
        max_spread = _parse_float(params.get("max_spread"))
        sort_key = params.get("sort", "score")
        active_filter = _parse_bool(params.get("active"))
        closed_filter = _parse_bool(params.get("closed"))
        tracked_only = _parse_bool(params.get("tracked"))

        latest = _latest_metrics_subquery()
        metrics = (
            select(MarketMetricsTS)
            .join(
                latest,
                and_(
                    MarketMetricsTS.condition_id == latest.c.condition_id,
                    MarketMetricsTS.ts == latest.c.max_ts,
                ),
            )
            .subquery()
        )

        query = select(
            Market,
            metrics.c.gamma_volume,
            metrics.c.gamma_liquidity,
            metrics.c.open_interest,
            metrics.c.spread_yes,
            metrics.c.spread_no,
        ).join(metrics, Market.condition_id == metrics.c.condition_id, isouter=True)
        if active_filter is not None:
            query = query.where(Market.active.is_(active_filter))
        if closed_filter is not None:
            query = query.where(Market.closed.is_(closed_filter))

        rows = session.execute(query).all()

        tracked_set = set(
            session.execute(
                select(TrackedMarket.condition_id).where(
                    TrackedMarket.enabled.is_(True)
                )
            )
            .scalars()
            .all()
        )

        items: list[dict[str, Any]] = []
        now = utc_now()
        for market, volume, liquidity, oi, spread_yes, spread_no in rows:
            tags = market.tag_ids or []
            if include_tags and not any(tag in tags for tag in include_tags):
                continue
            if exclude_tags and any(tag in tags for tag in exclude_tags):
                continue
            if tracked_only and market.condition_id not in tracked_set:
                continue

            if min_volume is not None and (volume is None or volume < min_volume):
                continue
            if min_liquidity is not None and (
                liquidity is None or liquidity < min_liquidity
            ):
                continue
            if min_oi is not None and (oi is None or oi < min_oi):
                continue
            spread_val = max(spread_yes or 0, spread_no or 0)
            if max_spread is not None and spread_val > max_spread:
                continue

            score = compute_market_score(
                volume, liquidity, oi, spread_yes, spread_no, settings
            )
            yes_token, no_token = resolve_binary_tokens(
                market.token_ids, market.outcomes
            )
            ends_soon = (
                market.end_time is not None
                and market.end_time <= now + timedelta(days=7)
            )

            items.append(
                {
                    "market": market,
                    "volume": volume,
                    "liquidity": liquidity,
                    "open_interest": oi,
                    "spread_yes": spread_yes,
                    "spread_no": spread_no,
                    "spread_max": spread_val,
                    "score": score,
                    "depth_yes": None,
                    "depth_no": None,
                    "yes_token": yes_token,
                    "no_token": no_token,
                    "tracked": market.condition_id in tracked_set,
                    "ends_soon": ends_soon,
                }
            )

        if sort_key == "volume":
            items.sort(key=lambda item: item["volume"] or 0, reverse=True)
        elif sort_key == "liquidity":
            items.sort(key=lambda item: item["liquidity"] or 0, reverse=True)
        elif sort_key == "oi":
            items.sort(key=lambda item: item["open_interest"] or 0, reverse=True)
        elif sort_key == "spread":
            items.sort(key=lambda item: item["spread_max"] or 0)
        elif sort_key == "newest":
            items.sort(
                key=lambda item: item["market"].created_at
                or item["market"].updated_at
                or item["market"].last_seen_at
                or utc_now(),
                reverse=True,
            )
        else:
            items.sort(key=lambda item: item["score"], reverse=True)

        items = items[:200]

        token_ids: list[str] = []
        for item in items:
            yes_token = item.get("yes_token")
            no_token = item.get("no_token")
            if yes_token:
                token_ids.append(yes_token)
            if no_token:
                token_ids.append(no_token)

        book_map: dict[str, list[dict[str, Any]]] = {}
        if token_ids:
            chunk_size = 900
            for i in range(0, len(token_ids), chunk_size):
                chunk = token_ids[i : i + chunk_size]
                books = (
                    session.execute(
                        select(OrderbookLevels).where(
                            OrderbookLevels.token_id.in_(chunk),
                            OrderbookLevels.side == OrderbookSide.ASK,
                        )
                    )
                    .scalars()
                    .all()
                )
                for book in books:
                    book_map[book.token_id] = book.levels

        for item in items:
            yes_token = item.get("yes_token")
            no_token = item.get("no_token")
            if yes_token:
                item["depth_yes"] = _depth_within_cents(
                    book_map.get(yes_token), settings.MARKET_DEPTH_WITHIN_CENTS
                )
            if no_token:
                item["depth_no"] = _depth_within_cents(
                    book_map.get(no_token), settings.MARKET_DEPTH_WITHIN_CENTS
                )
        return request.app.state.templates.TemplateResponse(
            "markets.html",
            {
                "request": request,
                "items": items,
                "filters": {
                    "include_tags": params.get("include_tags", ""),
                    "exclude_tags": params.get("exclude_tags", ""),
                    "min_volume": params.get("min_volume", ""),
                    "min_liquidity": params.get("min_liquidity", ""),
                    "min_oi": params.get("min_oi", ""),
                    "max_spread": params.get("max_spread", ""),
                    "sort": sort_key,
                    "active": params.get("active", ""),
                    "closed": params.get("closed", ""),
                    "tracked": params.get("tracked", ""),
                },
            },
        )
    finally:
        session.close()


@router.post("/markets/{condition_id}/track")
def track_market(
    request: Request, condition_id: str, next_url: str = Form("/markets")
) -> RedirectResponse:
    session = _session_from_request(request)
    try:
        row = session.get(TrackedMarket, condition_id)
        if row is None:
            row = TrackedMarket(
                condition_id=condition_id,
                enabled=True,
                source="manual",
                created_at=utc_now(),
            )
            session.add(row)
        else:
            row.enabled = True
        session.commit()
        return RedirectResponse(url=next_url, status_code=303)
    finally:
        session.close()


@router.post("/markets/{condition_id}/untrack")
def untrack_market(
    request: Request, condition_id: str, next_url: str = Form("/markets")
) -> RedirectResponse:
    session = _session_from_request(request)
    try:
        row = session.get(TrackedMarket, condition_id)
        if row is not None:
            row.enabled = False
            session.commit()
        return RedirectResponse(url=next_url, status_code=303)
    finally:
        session.close()


@router.get("/markets/{condition_id}", response_class=HTMLResponse)
def market_detail(request: Request, condition_id: str) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        settings = _settings_from_request(request)
        market = session.get(Market, condition_id)
        if market is None:
            raise HTTPException(status_code=404, detail="Market not found")

        yes_token, no_token = resolve_binary_tokens(market.token_ids, market.outcomes)
        orderbooks = (
            session.execute(
                select(OrderbookLevels)
                .where(OrderbookLevels.condition_id == condition_id)
                .order_by(OrderbookLevels.token_id, OrderbookLevels.side)
            )
            .scalars()
            .all()
        )

        book_map: dict[tuple[str, OrderbookSide], OrderbookLevels] = {
            (book.token_id, book.side): book for book in orderbooks
        }
        yes_asks = book_map.get((yes_token, OrderbookSide.ASK)) if yes_token else None
        no_asks = book_map.get((no_token, OrderbookSide.ASK)) if no_token else None

        arb_rows: list[dict[str, Any]] = []
        if yes_asks and no_asks:
            asks_yes = normalize_levels(yes_asks.levels)
            asks_no = normalize_levels(no_asks.levels)
            fee_bps = Decimal(str(settings.TAKER_FEE_BPS))
            for q in [50, 100, 500, 1000]:
                quantity = Decimal(str(q))
                avg_yes = avg_ask(asks_yes, quantity)
                avg_no = avg_ask(asks_no, quantity)
                edge = None
                if avg_yes is not None and avg_no is not None:
                    total = avg_yes + avg_no
                    fee = total * fee_bps / Decimal("10000")
                    edge = Decimal("1") - (total + fee)
                arb_rows.append(
                    {
                        "q": q,
                        "avg_yes": str(avg_yes) if avg_yes is not None else None,
                        "avg_no": str(avg_no) if avg_no is not None else None,
                        "edge": str(edge) if edge is not None else None,
                    }
                )

        trades = (
            session.execute(
                select(Trade)
                .where(Trade.condition_id == condition_id)
                .order_by(Trade.trade_ts.desc())
                .limit(50)
            )
            .scalars()
            .all()
        )

        signals = (
            session.execute(
                select(SignalEvent)
                .where(SignalEvent.condition_id == condition_id)
                .order_by(SignalEvent.created_at.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )

        tracked = session.get(TrackedMarket, condition_id)

        wallets_24h = _top_wallets(session, condition_id, timedelta(hours=24))
        wallets_7d = _top_wallets(session, condition_id, timedelta(days=7))

        return request.app.state.templates.TemplateResponse(
            "market_detail.html",
            {
                "request": request,
                "market": market,
                "yes_token": yes_token,
                "no_token": no_token,
                "orderbooks": orderbooks,
                "arb_rows": arb_rows,
                "trades": trades,
                "signals": signals,
                "tracked": tracked.enabled if tracked else False,
                "wallets_24h": wallets_24h,
                "wallets_7d": wallets_7d,
            },
        )
    finally:
        session.close()


@router.get("/arb", response_class=HTMLResponse)
def arb_screener(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        params = request.query_params
        min_edge = _parse_float(params.get("min_edge"))
        min_q = _parse_float(params.get("min_q"))
        sort_key = params.get("sort", "edge")

        signals = (
            session.execute(
                select(SignalEvent)
                .where(SignalEvent.signal_type == SignalType.ARB_BUY_BOTH)
                .order_by(SignalEvent.created_at.desc())
                .limit(500)
            )
            .scalars()
            .all()
        )

        condition_ids = {
            signal.condition_id for signal in signals if signal.condition_id
        }
        markets = (
            session.execute(
                select(Market).where(Market.condition_id.in_(condition_ids))
            )
            .scalars()
            .all()
        )
        market_map = {market.condition_id: market for market in markets}

        rows: list[dict[str, Any]] = []
        for signal in signals:
            payload = signal.payload
            edge = _parse_float(str(payload.get("edge_at_q_max")))
            q_max = _parse_float(str(payload.get("q_max")))
            if min_edge is not None and (edge is None or edge < min_edge):
                continue
            if min_q is not None and (q_max is None or q_max < min_q):
                continue
            rows.append(
                {
                    "signal": signal,
                    "market": market_map.get(signal.condition_id),
                    "edge": edge,
                    "q_max": q_max,
                }
            )

        if sort_key == "q_max":
            rows.sort(key=lambda row: row["q_max"] or 0, reverse=True)
        elif sort_key == "recent":
            rows.sort(key=lambda row: row["signal"].created_at, reverse=True)
        else:
            rows.sort(key=lambda row: row["edge"] or 0, reverse=True)

        rows = rows[:200]
        return request.app.state.templates.TemplateResponse(
            "arb.html",
            {
                "request": request,
                "rows": rows,
                "filters": {
                    "min_edge": params.get("min_edge", ""),
                    "min_q": params.get("min_q", ""),
                    "sort": sort_key,
                },
            },
        )
    finally:
        session.close()


@router.get("/whales", response_class=HTMLResponse)
def whale_tape(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        params = request.query_params
        min_notional = _parse_float(params.get("min_notional"))
        new_only = _parse_bool(params.get("new_only"))
        wallet_filter = params.get("wallet")
        market_filter = params.get("market")
        hours = _parse_float(params.get("hours")) or 24

        cutoff = utc_now() - timedelta(hours=hours)
        signals = (
            session.execute(
                select(SignalEvent)
                .where(
                    SignalEvent.signal_type.in_(
                        [
                            SignalType.LARGE_TAKER_TRADE,
                            SignalType.LARGE_NEW_WALLET_TRADE,
                        ]
                    ),
                    SignalEvent.created_at >= cutoff,
                )
                .order_by(SignalEvent.created_at.desc())
                .limit(500)
            )
            .scalars()
            .all()
        )

        rows: list[SignalEvent] = []
        for signal in signals:
            if new_only and signal.signal_type != SignalType.LARGE_NEW_WALLET_TRADE:
                continue
            if wallet_filter and signal.wallet != wallet_filter:
                continue
            if market_filter and signal.condition_id != market_filter:
                continue
            notional = _parse_float(str(signal.payload.get("notional_usd")))
            if min_notional is not None and (
                notional is None or notional < min_notional
            ):
                continue
            rows.append(signal)

        return request.app.state.templates.TemplateResponse(
            "whales.html",
            {
                "request": request,
                "signals": rows[:200],
                "filters": {
                    "min_notional": params.get("min_notional", ""),
                    "new_only": params.get("new_only", ""),
                    "wallet": params.get("wallet", ""),
                    "market": params.get("market", ""),
                    "hours": params.get("hours", ""),
                },
            },
        )
    finally:
        session.close()


@router.get("/wallets", response_class=HTMLResponse)
def wallets(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        params = request.query_params
        sort_key = params.get("sort", "last_seen")
        new_only = _parse_bool(params.get("new_only"))
        window_days = _parse_float(params.get("new_days"))
        settings = _settings_from_request(request)
        if window_days is None:
            window_days = settings.NEW_WALLET_WINDOW_DAYS

        query = select(Wallet)
        if new_only:
            cutoff = utc_now() - timedelta(days=window_days)
            query = query.where(Wallet.first_seen_at >= cutoff)

        if sort_key == "first_seen":
            query = query.order_by(Wallet.first_seen_at.desc())
        elif sort_key == "notional":
            query = query.order_by(Wallet.lifetime_notional_usd.desc())
        else:
            query = query.order_by(Wallet.last_seen_at.desc())

        rows = session.execute(query.limit(200)).scalars().all()
        return request.app.state.templates.TemplateResponse(
            "wallets.html",
            {
                "request": request,
                "wallets": rows,
                "filters": {
                    "sort": sort_key,
                    "new_only": params.get("new_only", ""),
                    "new_days": params.get("new_days", ""),
                },
            },
        )
    finally:
        session.close()


@router.get("/wallets/{wallet}", response_class=HTMLResponse)
def wallet_detail(request: Request, wallet: str) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        wallet_row = session.get(Wallet, wallet)
        if wallet_row is None:
            raise HTTPException(status_code=404, detail="Wallet not found")

        trades = (
            session.execute(
                select(Trade)
                .where(Trade.wallet == wallet)
                .order_by(Trade.trade_ts.desc())
                .limit(50)
            )
            .scalars()
            .all()
        )
        signals = (
            session.execute(
                select(SignalEvent)
                .where(SignalEvent.wallet == wallet)
                .order_by(SignalEvent.created_at.desc())
                .limit(50)
            )
            .scalars()
            .all()
        )
        positions = session.execute(
            select(WalletMarketExposure, Market)
            .join(
                Market,
                Market.condition_id == WalletMarketExposure.condition_id,
                isouter=True,
            )
            .where(WalletMarketExposure.wallet == wallet)
            .order_by(WalletMarketExposure.net_shares.desc())
        ).all()
        return request.app.state.templates.TemplateResponse(
            "wallet_detail.html",
            {
                "request": request,
                "wallet": wallet_row,
                "trades": trades,
                "signals": signals,
                "positions": positions,
            },
        )
    finally:
        session.close()


@router.get("/signals/{signal_id}", response_class=HTMLResponse)
def signal_detail(request: Request, signal_id: int) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        signal = session.get(SignalEvent, signal_id)
        if signal is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        payload = json.dumps(signal.payload, indent=2, sort_keys=True)
        return request.app.state.templates.TemplateResponse(
            "signal_detail.html",
            {
                "request": request,
                "signal": signal,
                "payload": payload,
            },
        )
    finally:
        session.close()


@router.get("/alerts", response_class=HTMLResponse)
def alerts(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        now = utc_now()
        signals = (
            session.execute(
                select(SignalEvent).order_by(SignalEvent.created_at.desc()).limit(200)
            )
            .scalars()
            .all()
        )
        signal_ids = [signal.id for signal in signals]
        logs = {}
        if signal_ids:
            latest = (
                select(
                    AlertLog.signal_event_id,
                    func.max(AlertLog.sent_at).label("max_sent"),
                )
                .where(AlertLog.signal_event_id.in_(signal_ids))
                .group_by(AlertLog.signal_event_id)
                .subquery()
            )
            rows = (
                session.execute(
                    select(AlertLog).join(
                        latest,
                        and_(
                            AlertLog.signal_event_id == latest.c.signal_event_id,
                            AlertLog.sent_at == latest.c.max_sent,
                        ),
                    )
                )
                .scalars()
                .all()
            )
            logs = {row.signal_event_id: row for row in rows}

        notification_keys = {build_notification_key(signal) for signal in signals}
        ack_map: dict[str, AlertAck] = {}
        if notification_keys:
            acks = (
                session.execute(
                    select(AlertAck)
                    .where(AlertAck.notification_key.in_(notification_keys))
                    .order_by(AlertAck.acked_until.desc())
                )
                .scalars()
                .all()
            )
            for ack in acks:
                if ack.notification_key in ack_map:
                    continue
                if ack.acked_until >= now:
                    ack_map[ack.notification_key] = ack

        rows = []
        for signal in signals:
            key = build_notification_key(signal)
            rows.append(
                {
                    "signal": signal,
                    "alert": logs.get(signal.id),
                    "notification_key": key,
                    "ack": ack_map.get(key),
                }
            )

        return request.app.state.templates.TemplateResponse(
            "alerts.html",
            {"request": request, "rows": rows},
        )
    finally:
        session.close()


@router.post("/alerts/ack")
def ack_alert(
    request: Request,
    notification_key: str = Form(...),
    hours: int = Form(1),
    created_by: str | None = Form(None),
) -> RedirectResponse:
    session = _session_from_request(request)
    try:
        acked_until = utc_now() + timedelta(hours=hours)
        ack = AlertAck(
            notification_key=notification_key,
            acked_until=acked_until,
            created_at=utc_now(),
            created_by=created_by,
        )
        session.add(ack)
        session.commit()
        return RedirectResponse(url="/alerts", status_code=303)
    finally:
        session.close()


@router.get("/alerts/rules", response_class=HTMLResponse)
def alert_rules(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        rules = (
            session.execute(select(AlertRule).order_by(AlertRule.priority.asc()))
            .scalars()
            .all()
        )
        return request.app.state.templates.TemplateResponse(
            "alert_rules.html",
            {"request": request, "rules": rules},
        )
    finally:
        session.close()


@router.post("/alerts/rules")
def save_alert_rule(
    request: Request,
    rule_id: int | None = Form(None),
    name: str | None = Form(None),
    priority: int = Form(100),
    enabled: str | None = Form(None),
    rule_json: str = Form("{}"),
) -> RedirectResponse:
    session = _session_from_request(request)
    try:
        try:
            rule_payload = json.loads(rule_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        now = utc_now()
        row = session.get(AlertRule, rule_id) if rule_id else None
        if row is None:
            row = AlertRule(created_at=now)
            session.add(row)
        row.name = name
        row.priority = priority
        row.enabled = enabled == "on"
        row.rule = rule_payload
        row.updated_at = now
        session.commit()
        return RedirectResponse(url="/alerts/rules", status_code=303)
    finally:
        session.close()


@router.post("/alerts/rules/{rule_id}/toggle")
def toggle_alert_rule(request: Request, rule_id: int) -> RedirectResponse:
    session = _session_from_request(request)
    try:
        row = session.get(AlertRule, rule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        row.enabled = not row.enabled
        row.updated_at = utc_now()
        session.commit()
        return RedirectResponse(url="/alerts/rules", status_code=303)
    finally:
        session.close()


@router.get("/config", response_class=HTMLResponse)
def config_page(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        rows = (
            session.execute(select(AppConfig).order_by(AppConfig.key)).scalars().all()
        )
        settings = _settings_from_request(request)
        return request.app.state.templates.TemplateResponse(
            "config.html",
            {"request": request, "rows": rows, "settings": settings},
        )
    finally:
        session.close()


@router.post("/config", response_class=HTMLResponse)
def update_config(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        try:
            parsed: Any = json.loads(value)
        except json.JSONDecodeError:
            parsed = value

        settings = load_settings(session)
        data = settings.model_dump()
        data[key] = parsed
        try:
            AppSettings.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        row = session.get(AppConfig, key)
        if row is None:
            row = AppConfig(key=key, value=parsed, updated_at=utc_now())
            session.add(row)
        else:
            row.value = parsed
            row.updated_at = utc_now()
        session.commit()

        settings = load_settings(session)
        request.app.state.settings = settings

        return RedirectResponse(url="/config", status_code=303)
    finally:
        session.close()


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    session = _session_from_request(request)
    try:
        session.execute(select(func.now()))
        return JSONResponse({"status": "ok"})
    finally:
        session.close()


@router.get("/readyz")
def readyz(request: Request) -> JSONResponse:
    session = _session_from_request(request)
    now = utc_now()
    try:
        trade_ts = session.execute(
            select(func.max(Trade.trade_ts))
        ).scalar_one_or_none()
        book_ts = session.execute(
            select(func.max(OrderbookLevels.as_of))
        ).scalar_one_or_none()

        trade_lag = (now - trade_ts).total_seconds() if trade_ts else None
        book_lag = (now - book_ts).total_seconds() if book_ts else None

        ready = True
        if trade_lag is None or trade_lag > 300:
            ready = False
        if book_lag is None or book_lag > 300:
            ready = False

        return JSONResponse(
            {
                "ready": ready,
                "trade_lag_seconds": trade_lag,
                "book_lag_seconds": book_lag,
            }
        )
    finally:
        session.close()


@router.get("/status", response_class=HTMLResponse)
def status(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        runs = session.execute(select(JobRun).order_by(JobRun.job_name)).scalars().all()
        recent_issues = (
            session.execute(
                select(DataQualityIssue)
                .order_by(DataQualityIssue.created_at.desc())
                .limit(10)
            )
            .scalars()
            .all()
        )
        return request.app.state.templates.TemplateResponse(
            "status.html",
            {"request": request, "runs": runs, "issues": recent_issues},
        )
    finally:
        session.close()


@router.get("/quality", response_class=HTMLResponse)
def quality(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        issues = (
            session.execute(
                select(DataQualityIssue)
                .order_by(DataQualityIssue.created_at.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )
        return request.app.state.templates.TemplateResponse(
            "quality.html",
            {"request": request, "issues": issues},
        )
    finally:
        session.close()
