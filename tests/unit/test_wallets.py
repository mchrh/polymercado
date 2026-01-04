from __future__ import annotations

from datetime import timedelta

from polymercado.config import AppSettings
from polymercado.models import Wallet
from polymercado.signals.wallets import is_dormant, is_new_wallet
from polymercado.utils import utc_now


def test_is_new_wallet_window():
    settings = AppSettings(NEW_WALLET_WINDOW_DAYS=7)
    now = utc_now()
    wallet = Wallet(
        wallet="0x1",
        first_seen_at=now - timedelta(days=3),
        last_seen_at=now,
        first_trade_ts=now,
        lifetime_notional_usd=1,
    )
    assert is_new_wallet(wallet, now, settings) is True


def test_is_dormant_window():
    settings = AppSettings(DORMANT_WINDOW_DAYS=7)
    now = utc_now()
    wallet = Wallet(
        wallet="0x1",
        first_seen_at=now - timedelta(days=30),
        last_seen_at=now - timedelta(days=10),
        first_trade_ts=now - timedelta(days=30),
        lifetime_notional_usd=1,
    )
    assert is_dormant(wallet, now, settings) is True
