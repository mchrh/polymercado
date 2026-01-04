from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from polymercado.config import AppSettings
from polymercado.utils import to_decimal


@dataclass(frozen=True)
class Level:
    price: Decimal
    size: Decimal


def normalize_levels(levels: Iterable[dict[str, str]]) -> list[Level]:
    normalized: list[Level] = []
    for level in levels:
        price = to_decimal(level.get("price"))
        size = to_decimal(level.get("size"))
        if price is None or size is None:
            continue
        if price <= 0 or size <= 0:
            continue
        normalized.append(Level(price=price, size=size))
    return normalized


def avg_ask(levels: list[Level], quantity: Decimal) -> Decimal | None:
    remaining = quantity
    cost = Decimal("0")
    for level in levels:
        if remaining <= 0:
            break
        fill = min(remaining, level.size)
        cost += fill * level.price
        remaining -= fill
    if remaining > 0:
        return None
    return cost / quantity


def fill_levels(levels: list[Level], quantity: Decimal) -> list[dict[str, str]]:
    remaining = quantity
    used: list[dict[str, str]] = []
    for level in levels:
        if remaining <= 0:
            break
        fill = min(remaining, level.size)
        used.append({"price": str(level.price), "size": str(fill)})
        remaining -= fill
    return used


def candidate_quantities(levels: list[Level], max_shares: Decimal) -> list[Decimal]:
    quantities: list[Decimal] = []
    total = Decimal("0")
    for level in levels:
        total += level.size
        if total > max_shares:
            total = max_shares
        quantities.append(total)
        if total >= max_shares:
            break
    return quantities


def compute_arb(
    asks_yes: list[Level],
    asks_no: list[Level],
    settings: AppSettings,
) -> dict[str, Decimal | None]:
    min_q = Decimal(str(settings.ARB_MIN_EXECUTABLE_SHARES))
    max_q = Decimal(str(settings.ARB_MAX_SHARES_TO_EVALUATE))
    edge_min = Decimal(str(settings.ARB_EDGE_MIN))
    fee_bps = Decimal(str(settings.TAKER_FEE_BPS))

    candidates = set(candidate_quantities(asks_yes, max_q)) | set(
        candidate_quantities(asks_no, max_q)
    )
    candidates.add(min_q)
    candidates.add(max_q)
    sorted_candidates = sorted(q for q in candidates if q >= min_q)

    def total_cost(avg_yes: Decimal, avg_no: Decimal) -> Decimal:
        base = avg_yes + avg_no
        fee = base * fee_bps / Decimal("10000")
        return base + fee

    edge_at_min = None
    avg_yes_min = avg_ask(asks_yes, min_q)
    avg_no_min = avg_ask(asks_no, min_q)
    if avg_yes_min is not None and avg_no_min is not None:
        total = total_cost(avg_yes_min, avg_no_min)
        edge_at_min = Decimal("1") - total

    q_max = None
    edge_at_q_max = None
    avg_yes_at_q_max = None
    avg_no_at_q_max = None

    for q in sorted_candidates:
        avg_yes = avg_ask(asks_yes, q)
        avg_no = avg_ask(asks_no, q)
        if avg_yes is None or avg_no is None:
            continue
        total = total_cost(avg_yes, avg_no)
        edge = Decimal("1") - total
        if edge > edge_min:
            q_max = q
            edge_at_q_max = edge
            avg_yes_at_q_max = avg_yes
            avg_no_at_q_max = avg_no

    return {
        "q_max": q_max,
        "edge_at_min_q": edge_at_min,
        "edge_at_q_max": edge_at_q_max,
        "avg_ask_yes_at_q_max": avg_yes_at_q_max,
        "avg_ask_no_at_q_max": avg_no_at_q_max,
    }
