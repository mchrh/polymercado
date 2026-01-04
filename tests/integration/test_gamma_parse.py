from __future__ import annotations

import json
from pathlib import Path

from polymercado.ingestion.gamma import parse_market


def test_parse_gamma_market_fields():
    fixture_path = Path("tests/fixtures/polymarket/gamma_events.json")
    payload = json.loads(fixture_path.read_text())
    assert payload

    event = payload[0]
    assert event.get("markets")
    market = event["markets"][0]

    parsed = parse_market(market, event)
    assert parsed["condition_id"]
    assert parsed["token_ids"]
    assert isinstance(parsed["token_ids"], list)
