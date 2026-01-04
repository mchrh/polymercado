from __future__ import annotations

from decimal import Decimal

from polymercado.config import AppSettings
from polymercado.signals.arb import avg_ask, compute_arb, normalize_levels


def test_avg_ask_partial_fill():
    levels = normalize_levels(
        [
            {"price": "0.50", "size": "10"},
            {"price": "0.60", "size": "10"},
        ]
    )
    avg = avg_ask(levels, Decimal("15"))
    assert avg is not None
    assert round(float(avg), 4) == 0.5333


def test_compute_arb_detects_edge():
    settings = AppSettings(ARB_EDGE_MIN=0.01, ARB_MIN_EXECUTABLE_SHARES=50)
    asks_yes = normalize_levels([{"price": "0.49", "size": "100"}])
    asks_no = normalize_levels([{"price": "0.49", "size": "100"}])

    result = compute_arb(asks_yes, asks_no, settings)
    assert result["q_max"] == Decimal("100")
    assert result["edge_at_q_max"] is not None
    assert result["edge_at_q_max"] > Decimal("0.01")
