from __future__ import annotations

from polymercado.alerts.dispatcher import rule_matches
from polymercado.models import SignalEvent, SignalType
from polymercado.utils import utc_now


def _signal(payload: dict[str, object]) -> SignalEvent:
    return SignalEvent(
        signal_type=SignalType.LARGE_TAKER_TRADE,
        dedupe_key="test",
        created_at=utc_now(),
        severity=3,
        payload=payload,
    )


def test_rule_matches_payload_any():
    signal = _signal({"market_tag_slugs": ["sports", "crypto"]})
    rule = {"when": {"payload_any": {"market_tag_slugs": ["sports"]}}}
    assert rule_matches(rule, signal, utc_now()) is True


def test_rule_matches_payload_not_any():
    signal = _signal({"market_tag_labels": ["Sports", "US Elections"]})
    rule = {"when": {"payload_not_any": {"market_tag_labels": ["sports"]}}}
    assert rule_matches(rule, signal, utc_now()) is False


def test_rule_matches_payload_eq_bool():
    signal = _signal({"market_is_sport": False})
    rule = {"when": {"payload_eq": {"market_is_sport": False}}}
    assert rule_matches(rule, signal, utc_now()) is True
