from __future__ import annotations

import json
from pathlib import Path

from polymercado.ingestion.clob import upsert_orderbook
from polymercado.models import OrderbookLevels, OrderbookSide


def test_upsert_orderbook(session):
    fixture_path = Path("tests/fixtures/polymarket/clob_book.json")
    payload = json.loads(fixture_path.read_text())

    upsert_orderbook(session, payload)
    session.commit()

    bids = session.get(
        OrderbookLevels,
        {"token_id": payload["asset_id"], "side": OrderbookSide.BID},
    )
    asks = session.get(
        OrderbookLevels,
        {"token_id": payload["asset_id"], "side": OrderbookSide.ASK},
    )
    assert bids is not None
    assert asks is not None
