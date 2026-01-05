from __future__ import annotations

from polymercado.alerts.dispatcher import format_message
from polymercado.models import SignalEvent, SignalType
from polymercado.utils import utc_now


def test_format_message_includes_outcome_for_trades():
    signal = SignalEvent(
        signal_type=SignalType.LARGE_TAKER_TRADE,
        dedupe_key="test",
        created_at=utc_now(),
        severity=3,
        payload={
            "side": "BUY",
            "outcome": "Yes",
            "notional_usd": 10000,
            "price": 0.62,
            "market_title": "Test Market",
        },
    )
    message = format_message(signal)
    assert "BUY" in message
    assert "Yes" in message
    assert "$10,000" in message
    assert "@0.62" in message
    assert "Test Market" in message


def test_format_message_includes_new_wallet_prefix():
    signal = SignalEvent(
        signal_type=SignalType.LARGE_NEW_WALLET_TRADE,
        dedupe_key="test-new",
        created_at=utc_now(),
        severity=4,
        payload={"side": "SELL", "outcome": "No", "notional_usd": 50000},
    )
    message = format_message(signal)
    assert "New wallet trade" in message
