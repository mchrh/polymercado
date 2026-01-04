from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from polymercado.utils import ensure_utc, parse_datetime


def trade_dedupe_key(trade: dict[str, Any]) -> str:
    tx_hash = trade.get("transactionHash")
    if tx_hash:
        return f"tx:{tx_hash}"

    parts = [
        trade.get("proxyWallet"),
        trade.get("conditionId"),
        trade.get("asset"),
        trade.get("side"),
        trade.get("timestamp"),
        trade.get("size"),
        trade.get("price"),
    ]
    raw = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


def parse_trade_ts(value: Any) -> datetime | None:
    return ensure_utc(parse_datetime(value))


def compute_notional(price: Decimal | None, size: Decimal | None) -> Decimal | None:
    if price is None or size is None:
        return None
    return price * size
