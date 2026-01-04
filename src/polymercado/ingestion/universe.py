from __future__ import annotations

from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.markets import compute_market_score
from polymercado.models import Market, MarketMetricsTS, TrackedMarket


def select_tracked_markets(session: Session, settings: AppSettings) -> Sequence[str]:
    metrics_latest = (
        select(
            MarketMetricsTS.condition_id,
            func.max(MarketMetricsTS.ts).label("max_ts"),
        )
        .group_by(MarketMetricsTS.condition_id)
        .subquery()
    )

    metrics = (
        select(MarketMetricsTS)
        .join(
            metrics_latest,
            and_(
                MarketMetricsTS.condition_id == metrics_latest.c.condition_id,
                MarketMetricsTS.ts == metrics_latest.c.max_ts,
            ),
        )
        .subquery()
    )

    query = select(
        Market.condition_id,
        Market.active,
        Market.closed,
        metrics.c.gamma_volume,
        metrics.c.gamma_liquidity,
        metrics.c.open_interest,
        metrics.c.spread_yes,
        metrics.c.spread_no,
    ).join(metrics, Market.condition_id == metrics.c.condition_id, isouter=True)

    rows = session.execute(query).all()

    auto: list[tuple[float, str]] = []
    for row in rows:
        active = row.active
        closed = row.closed
        if active is False or closed is True:
            continue

        has_metrics = (
            row.gamma_volume is not None
            or row.gamma_liquidity is not None
            or row.open_interest is not None
        )
        meets_threshold = (
            (
                row.gamma_volume is not None
                and row.gamma_volume >= settings.MIN_GAMMA_VOLUME
            )
            or (
                row.gamma_liquidity is not None
                and row.gamma_liquidity >= settings.MIN_GAMMA_LIQUIDITY
            )
            or (
                row.open_interest is not None
                and row.open_interest >= settings.MIN_OPEN_INTEREST
            )
        )
        if has_metrics and not meets_threshold:
            continue
        auto.append(
            (
                compute_market_score(
                    row.gamma_volume,
                    row.gamma_liquidity,
                    row.open_interest,
                    row.spread_yes,
                    row.spread_no,
                    settings,
                ),
                row.condition_id,
            )
        )

    auto.sort(key=lambda item: item[0], reverse=True)

    manual = (
        session.execute(
            select(TrackedMarket.condition_id).where(TrackedMarket.enabled.is_(True))
        )
        .scalars()
        .all()
    )

    picked: list[str] = []
    seen: set[str] = set()
    for condition_id in manual:
        if condition_id in seen:
            continue
        seen.add(condition_id)
        picked.append(condition_id)
        if len(picked) >= settings.MAX_TRACKED_MARKETS:
            return picked

    for _, condition_id in auto:
        if condition_id in seen:
            continue
        seen.add(condition_id)
        picked.append(condition_id)
        if len(picked) >= settings.MAX_TRACKED_MARKETS:
            break

    return picked
