from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from polymercado.config import AppSettings, load_settings
from polymercado.models import (
    AlertLog,
    AppConfig,
    JobRun,
    Market,
    MarketMetricsTS,
    OrderbookLevels,
    SignalEvent,
    SignalType,
    Trade,
    Wallet,
)
from polymercado.utils import utc_now

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


@router.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse(url="/markets")


@router.get("/markets", response_class=HTMLResponse)
def markets(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
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

        query = (
            select(
                Market,
                metrics.c.gamma_volume,
                metrics.c.gamma_liquidity,
                metrics.c.open_interest,
                metrics.c.spread_yes,
                metrics.c.spread_no,
            )
            .join(metrics, Market.condition_id == metrics.c.condition_id, isouter=True)
            .order_by(Market.last_seen_at.desc().nullslast())
            .limit(200)
        )

        rows = session.execute(query).all()
        return request.app.state.templates.TemplateResponse(
            "markets.html",
            {"request": request, "rows": rows},
        )
    finally:
        session.close()


@router.get("/markets/{condition_id}", response_class=HTMLResponse)
def market_detail(request: Request, condition_id: str) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        market = session.get(Market, condition_id)
        if market is None:
            raise HTTPException(status_code=404, detail="Market not found")

        orderbooks = (
            session.execute(
                select(OrderbookLevels)
                .where(OrderbookLevels.condition_id == condition_id)
                .order_by(OrderbookLevels.token_id, OrderbookLevels.side)
            )
            .scalars()
            .all()
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

        return request.app.state.templates.TemplateResponse(
            "market_detail.html",
            {
                "request": request,
                "market": market,
                "orderbooks": orderbooks,
                "trades": trades,
                "signals": signals,
            },
        )
    finally:
        session.close()


@router.get("/arb", response_class=HTMLResponse)
def arb_screener(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        signals = (
            session.execute(
                select(SignalEvent)
                .where(SignalEvent.signal_type == SignalType.ARB_BUY_BOTH)
                .order_by(SignalEvent.created_at.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )
        return request.app.state.templates.TemplateResponse(
            "arb.html",
            {"request": request, "signals": signals},
        )
    finally:
        session.close()


@router.get("/whales", response_class=HTMLResponse)
def whale_tape(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        signals = (
            session.execute(
                select(SignalEvent)
                .where(
                    SignalEvent.signal_type.in_(
                        [
                            SignalType.LARGE_TAKER_TRADE,
                            SignalType.LARGE_NEW_WALLET_TRADE,
                        ]
                    )
                )
                .order_by(SignalEvent.created_at.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )
        return request.app.state.templates.TemplateResponse(
            "whales.html",
            {"request": request, "signals": signals},
        )
    finally:
        session.close()


@router.get("/wallets", response_class=HTMLResponse)
def wallets(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        rows = (
            session.execute(
                select(Wallet).order_by(Wallet.last_seen_at.desc()).limit(200)
            )
            .scalars()
            .all()
        )
        return request.app.state.templates.TemplateResponse(
            "wallets.html",
            {"request": request, "wallets": rows},
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
        return request.app.state.templates.TemplateResponse(
            "wallet_detail.html",
            {
                "request": request,
                "wallet": wallet_row,
                "trades": trades,
                "signals": signals,
            },
        )
    finally:
        session.close()


@router.get("/alerts", response_class=HTMLResponse)
def alerts(request: Request) -> HTMLResponse:
    session = _session_from_request(request)
    try:
        rows = session.execute(
            select(SignalEvent, AlertLog)
            .join(AlertLog, AlertLog.signal_event_id == SignalEvent.id, isouter=True)
            .order_by(SignalEvent.created_at.desc())
            .limit(200)
        ).all()
        return request.app.state.templates.TemplateResponse(
            "alerts.html",
            {"request": request, "rows": rows},
        )
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
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value

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
        return request.app.state.templates.TemplateResponse(
            "status.html",
            {"request": request, "runs": runs},
        )
    finally:
        session.close()
