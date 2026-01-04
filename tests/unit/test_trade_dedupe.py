from __future__ import annotations

from polymercado.trades import trade_dedupe_key


def test_trade_dedupe_prefers_tx_hash():
    trade = {"transactionHash": "0xabc"}
    assert trade_dedupe_key(trade) == "tx:0xabc"


def test_trade_dedupe_hash_fallback():
    trade = {
        "proxyWallet": "0x1",
        "conditionId": "0x2",
        "asset": "123",
        "side": "BUY",
        "timestamp": 123,
        "size": "10",
        "price": "0.5",
    }
    dedupe = trade_dedupe_key(trade)
    assert dedupe.startswith("hash:")
