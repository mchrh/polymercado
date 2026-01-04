from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from polymercado.models import AppConfig


class AppSettings(BaseModel):
    DATABASE_URL: str = Field(default="sqlite:///./polymercado.db")

    HTTP_TIMEOUT_SECONDS: float = 10.0
    HTTP_MAX_CONCURRENCY: int = 10

    SYNC_GAMMA_EVENTS_INTERVAL_SECONDS: int = 600
    SYNC_TRADES_INTERVAL_SECONDS: int = 45
    SYNC_OI_INTERVAL_SECONDS: int = 300
    SYNC_UNIVERSE_INTERVAL_SECONDS: int = 900
    ORDERBOOK_SNAPSHOT_INTERVAL_SECONDS: int = 300

    GAMMA_EVENTS_PAGE_LIMIT: int = 100
    GAMMA_EVENTS_MAX_PAGES: int = 50

    MAX_TRACKED_MARKETS: int = 200
    MIN_GAMMA_VOLUME: float = 50000
    MIN_GAMMA_LIQUIDITY: float = 10000
    MIN_OPEN_INTEREST: float = 5000

    TAKER_ONLY: bool = True
    LARGE_TRADE_USD_THRESHOLD: float = 10000
    NEW_WALLET_WINDOW_DAYS: int = 14
    DORMANT_WINDOW_DAYS: int = 30
    TRACK_WALLET_DAYS_AFTER_LARGE_TRADE: int = 7

    TRADE_SAFETY_WINDOW_SECONDS: int = 300
    TRADES_PAGE_LIMIT: int = 500

    ARB_EDGE_MIN: float = 0.01
    ARB_MIN_EXECUTABLE_SHARES: float = 50
    ARB_MAX_SHARES_TO_EVALUATE: float = 5000
    ARB_MAX_BOOK_AGE_SECONDS: int = 10
    ARB_MARKET_COOLDOWN_SECONDS: int = 60
    TAKER_FEE_BPS: int = 0

    ALERTS_ENABLED: bool = False
    ALERT_CHANNELS: str = ""
    ALERT_DEDUP_WINDOW_SECONDS: int = 600
    ALERT_MIN_SEVERITY: int = 2
    ALERT_SLACK_WEBHOOK_URL: str | None = None
    ALERT_TELEGRAM_BOT_TOKEN: str | None = None
    ALERT_TELEGRAM_CHAT_ID: str | None = None

    SCHEDULER_ENABLED: bool = True
    CLOB_WS_ENABLED: bool = False
    CLOB_WS_PING_SECONDS: int = 10

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
