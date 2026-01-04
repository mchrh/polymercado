from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from polymercado.models import AppConfig


class AppSettings(BaseModel):
    DATABASE_URL: str = Field(default="sqlite:///./polymercado.db")

    HTTP_TIMEOUT_SECONDS: float = Field(default=10.0, ge=1)
    HTTP_MAX_CONCURRENCY: int = Field(default=10, ge=1)

    SYNC_GAMMA_EVENTS_INTERVAL_SECONDS: int = Field(default=600, ge=1)
    SYNC_TRADES_INTERVAL_SECONDS: int = Field(default=45, ge=1)
    SYNC_OI_INTERVAL_SECONDS: int = Field(default=300, ge=1)
    SYNC_UNIVERSE_INTERVAL_SECONDS: int = Field(default=900, ge=1)
    ORDERBOOK_SNAPSHOT_INTERVAL_SECONDS: int = Field(default=300, ge=1)
    SYNC_POSITIONS_INTERVAL_SECONDS: int = Field(default=600, ge=1)

    GAMMA_EVENTS_PAGE_LIMIT: int = Field(default=100, ge=1)
    GAMMA_EVENTS_MAX_PAGES: int = Field(default=50, ge=1)

    MAX_TRACKED_MARKETS: int = Field(default=200, ge=1)
    MIN_GAMMA_VOLUME: float = Field(default=50000, ge=0)
    MIN_GAMMA_LIQUIDITY: float = Field(default=10000, ge=0)
    MIN_OPEN_INTEREST: float = Field(default=5000, ge=0)

    MARKET_SCORE_W1: float = Field(default=1.0, ge=0)
    MARKET_SCORE_W2: float = Field(default=1.0, ge=0)
    MARKET_SCORE_W3: float = Field(default=1.5, ge=0)
    MARKET_SCORE_W4: float = Field(default=0.5, ge=0)
    MARKET_DEPTH_WITHIN_CENTS: float = Field(default=0.01, ge=0)

    TAKER_ONLY: bool = True
    LARGE_TRADE_USD_THRESHOLD: float = Field(default=10000, ge=0)
    NEW_WALLET_WINDOW_DAYS: int = Field(default=14, ge=0)
    DORMANT_WINDOW_DAYS: int = Field(default=30, ge=0)
    TRACK_WALLET_DAYS_AFTER_LARGE_TRADE: int = Field(default=7, ge=0)
    WALLET_POSITIONS_ENABLED: bool = True
    POSITIONS_PAGE_LIMIT: int = Field(default=200, ge=1)
    POSITIONS_SIZE_THRESHOLD: float = Field(default=1.0, ge=0)

    TRADE_SAFETY_WINDOW_SECONDS: int = Field(default=300, ge=0)
    TRADES_PAGE_LIMIT: int = Field(default=500, ge=1)
    TRADES_MAX_PAGES: int = Field(default=10, ge=1)
    TRADES_INITIAL_LOOKBACK_HOURS: int = Field(default=24, ge=1)

    ARB_EDGE_MIN: float = Field(default=0.01, ge=0, le=0.05)
    ARB_MIN_EXECUTABLE_SHARES: float = Field(default=50, ge=1)
    ARB_MAX_SHARES_TO_EVALUATE: float = Field(default=5000, ge=1)
    ARB_MAX_BOOK_AGE_SECONDS: int = Field(default=10, ge=1)
    ARB_MARKET_COOLDOWN_SECONDS: int = Field(default=60, ge=0)
    TAKER_FEE_BPS: int = Field(default=0, ge=0, le=1000)

    ALERTS_ENABLED: bool = True
    ALERT_CHANNELS: str = "telegram"
    ALERT_DEDUP_WINDOW_SECONDS: int = Field(default=600, ge=0)
    ALERT_MIN_SEVERITY: int = Field(default=3, ge=1, le=5)
    ALERT_RULES_ENABLED: bool = True
    ALERT_ACK_ENABLED: bool = True
    ALERT_SLACK_WEBHOOK_URL: str | None = None
    ALERT_TELEGRAM_BOT_TOKEN: str | None = "8552168176:AAFAmrs7ngmxJfweaeRiAxmDeS0VE7L85pU"
    ALERT_TELEGRAM_CHAT_ID: str | None = "-3591884093"

    SCHEDULER_ENABLED: bool = True
    CLOB_WS_ENABLED: bool = True
    CLOB_WS_PING_SECONDS: int = Field(default=10, ge=1)
    CLOB_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    CLOB_WS_FALLBACK_URLS: str = "wss://ws-subscriptions-clob.polymarket.com/ws/"
    CLOB_WS_MAX_ASSETS: int = Field(default=400, ge=1)

    DATA_QUALITY_ENABLED: bool = True
    DATA_QUALITY_INTERVAL_SECONDS: int = Field(default=3600, ge=60)
    DATA_QUALITY_TRADE_SAMPLE_LIMIT: int = Field(default=200, ge=1)
    DATA_QUALITY_MAX_NEW_WALLETS_PER_HOUR: int = Field(default=500, ge=0)

    def config_snapshot(self, keys: list[str]) -> dict[str, Any]:
        data = self.model_dump()
        return {key: data[key] for key in keys if key in data}


def load_settings(session: Session | None = None) -> AppSettings:
    data = AppSettings().model_dump()

    if session is not None:
        rows = session.execute(select(AppConfig)).scalars().all()
        for row in rows:
            data[row.key] = row.value

    for key in list(data.keys()):
        if key in os.environ:
            data[key] = os.environ[key]

    return AppSettings.model_validate(data)
