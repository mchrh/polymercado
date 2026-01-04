from __future__ import annotations


import math

from polymercado.config import AppSettings
from polymercado.utils import safe_lower


def resolve_binary_tokens(
    token_ids: list[str] | None, outcomes: list[str] | None
) -> tuple[str | None, str | None]:
    if not token_ids or len(token_ids) != 2:
        return None, None
    if outcomes and len(outcomes) == 2:
        lower = [safe_lower(outcome) for outcome in outcomes]
        if "yes" in lower and "no" in lower:
            yes_index = lower.index("yes")
            no_index = lower.index("no")
            return token_ids[yes_index], token_ids[no_index]
    return token_ids[0], token_ids[1]


def compute_market_score(
    volume: float | None,
    liquidity: float | None,
    open_interest: float | None,
    spread_yes: float | None,
    spread_no: float | None,
    settings: AppSettings,
) -> float:
    volume_value = float(volume or 0)
    liquidity_value = float(liquidity or 0)
    oi_value = float(open_interest or 0)
    spread_penalty = float(spread_yes or 0) + float(spread_no or 0)
    return (
        settings.MARKET_SCORE_W1 * math.log1p(volume_value)
        + settings.MARKET_SCORE_W2 * math.log1p(liquidity_value)
        + settings.MARKET_SCORE_W3 * math.log1p(oi_value)
        - settings.MARKET_SCORE_W4 * spread_penalty
    )
