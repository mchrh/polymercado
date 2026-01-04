from __future__ import annotations

from functools import partial

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import sessionmaker

from polymercado.alerts.dispatcher import dispatch_alerts
from polymercado.config import AppSettings
from polymercado.ingestion.clob import sync_orderbooks
from polymercado.ingestion.data_api import sync_large_trades, sync_open_interest
from polymercado.ingestion.gamma import sync_gamma_events
from polymercado.jobs import run_job
from polymercado.signals.engine import run_signal_engine


def build_scheduler(
    settings: AppSettings, session_factory: sessionmaker
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def with_session(job_name, func):
        def runner():
            session = session_factory()
            try:
                run_job(session, job_name, func)
            finally:
                session.close()

        return runner

    scheduler.add_job(
        with_session(
            "sync_gamma_events", partial(sync_gamma_events, settings=settings)
        ),
        "interval",
        seconds=settings.SYNC_GAMMA_EVENTS_INTERVAL_SECONDS,
        id="sync_gamma_events",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        with_session(
            "sync_open_interest", partial(sync_open_interest, settings=settings)
        ),
        "interval",
        seconds=settings.SYNC_OI_INTERVAL_SECONDS,
        id="sync_open_interest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        with_session(
            "sync_large_trades", partial(sync_large_trades, settings=settings)
        ),
        "interval",
        seconds=settings.SYNC_TRADES_INTERVAL_SECONDS,
        id="sync_large_trades",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        with_session("sync_orderbooks", partial(sync_orderbooks, settings=settings)),
        "interval",
        seconds=settings.ORDERBOOK_SNAPSHOT_INTERVAL_SECONDS,
        id="sync_orderbooks",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        with_session(
            "run_signal_engine", partial(run_signal_engine, settings=settings)
        ),
        "interval",
        seconds=settings.SYNC_TRADES_INTERVAL_SECONDS,
        id="run_signal_engine",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        with_session("dispatch_alerts", partial(dispatch_alerts, settings=settings)),
        "interval",
        seconds=60,
        id="dispatch_alerts",
        max_instances=1,
        coalesce=True,
    )

    return scheduler
