from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderbookSide(str, enum.Enum):
    BID = "BID"
    ASK = "ASK"


class SignalType(str, enum.Enum):
    LARGE_TAKER_TRADE = "LARGE_TAKER_TRADE"
    LARGE_NEW_WALLET_TRADE = "LARGE_NEW_WALLET_TRADE"
    DORMANT_WALLET_REACTIVATION = "DORMANT_WALLET_REACTIVATION"
    ARB_BUY_BOTH = "ARB_BUY_BOTH"
    NEW_MARKET = "NEW_MARKET"


class AlertStatus(str, enum.Enum):
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class Market(Base):
    __tablename__ = "markets"

    condition_id: Mapped[str] = mapped_column(String, primary_key=True)
    market_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    slug: Mapped[str | None] = mapped_column(String, nullable=True)
    question: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    tag_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    neg_risk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcomes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    token_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketMetricsTS(Base):
    __tablename__ = "market_metrics_ts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(
        String, ForeignKey("markets.condition_id")
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    gamma_volume: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    gamma_liquidity: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    best_bid_yes: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    best_ask_yes: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    best_bid_no: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    best_ask_no: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    spread_yes: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    spread_no: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)

    __table_args__ = (Index("ix_metrics_condition_ts", "condition_id", "ts"),)


class OrderbookLevels(Base):
    __tablename__ = "orderbook_levels"

    token_id: Mapped[str] = mapped_column(String, primary_key=True)
    side: Mapped[OrderbookSide] = mapped_column(
        Enum(OrderbookSide, native_enum=False), primary_key=True
    )
    condition_id: Mapped[str] = mapped_column(String, nullable=False)
    levels: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    tick_size: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    min_order_size: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    neg_risk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (Index("ix_orderbook_condition", "condition_id"),)


class Trade(Base):
    __tablename__ = "trades"

    trade_pk: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    wallet: Mapped[str | None] = mapped_column(String, nullable=True)
    condition_id: Mapped[str] = mapped_column(String, nullable=False)
    token_id: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide, native_enum=False))
    price: Mapped[float] = mapped_column(Numeric(18, 8))
    size: Mapped[float] = mapped_column(Numeric(18, 8))
    notional_usd: Mapped[float] = mapped_column(Numeric(18, 8))
    trade_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("transaction_hash", name="uq_trades_tx_hash"),
        Index("ix_trades_trade_ts", "trade_ts"),
        Index("ix_trades_wallet_trade_ts", "wallet", "trade_ts"),
        Index("ix_trades_condition_trade_ts", "condition_id", "trade_ts"),
        Index("ix_trades_notional", "notional_usd"),
    )


class Wallet(Base):
    __tablename__ = "wallets"

    wallet: Mapped[str] = mapped_column(String, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_trade_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifetime_notional_usd: Mapped[float] = mapped_column(Numeric(24, 8))
    last_7d_notional_usd: Mapped[float | None] = mapped_column(
        Numeric(24, 8), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class WalletMarketExposure(Base):
    __tablename__ = "wallet_market_exposure"

    wallet: Mapped[str] = mapped_column(
        String, ForeignKey("wallets.wallet"), primary_key=True
    )
    condition_id: Mapped[str] = mapped_column(String, primary_key=True)
    net_shares: Mapped[float] = mapped_column(Numeric(18, 8))
    avg_entry_price: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalEvent(Base):
    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_type: Mapped[SignalType] = mapped_column(Enum(SignalType, native_enum=False))
    dedupe_key: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    severity: Mapped[int] = mapped_column(Integer)
    wallet: Mapped[str | None] = mapped_column(String, nullable=True)
    condition_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_signal_type_created", "signal_type", "created_at"),
        Index("ix_signal_wallet_created", "wallet", "created_at"),
        Index("ix_signal_condition_created", "condition_id", "created_at"),
    )


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("signal_events.id")
    )
    channel: Mapped[str] = mapped_column(String)
    notification_key: Mapped[str] = mapped_column(String)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus, native_enum=False))
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_alert_notification_sent", "notification_key", "sent_at"),
    )


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)


class JobRun(Base):
    __tablename__ = "job_runs"

    job_name: Mapped[str] = mapped_column(String, primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_duration_ms: Mapped[float | None] = mapped_column(
        Numeric(18, 3), nullable=True
    )
