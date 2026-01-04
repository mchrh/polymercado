from __future__ import annotations

from typing import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from polymercado.config import AppSettings
from polymercado.models import Market, MarketMetricsTS


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

    query = (
        select(Market.condition_id)
        .join(metrics, Market.condition_id == metrics.c.condition_id, isouter=True)
        .where(
            or_(
                metrics.c.gamma_volume >= settings.MIN_GAMMA_VOLUME,
                metrics.c.gamma_liquidity >= settings.MIN_GAMMA_LIQUIDITY,
                metrics.c.open_interest >= settings.MIN_OPEN_INTEREST,
                metrics.c.condition_id.is_(None),
            )
        )
        .limit(settings.MAX_TRACKED_MARKETS)
    )

    return [row[0] for row in session.execute(query).all()]
