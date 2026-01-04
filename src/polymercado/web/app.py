from __future__ import annotations

from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from polymercado.config import load_settings
from polymercado.db import get_session_factory, init_db
from polymercado.ingestion.gamma import sync_gamma_events, sync_tag_metadata
from polymercado.jobs import run_job
from polymercado.logging import get_logger, setup_logging
from polymercado.ingestion.clob_ws import OrderbookWebsocket
from polymercado.models import Market, Tag
from polymercado.scheduler import build_scheduler
from polymercado.web.routes import router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    base_settings = load_settings()
    init_db(base_settings)

    session_factory = get_session_factory(base_settings.DATABASE_URL)
    session = session_factory()
    try:
        settings = load_settings(session)
        has_tags = session.execute(select(Tag.id).limit(1)).first() is not None
        has_markets = (
            session.execute(select(Market.condition_id).limit(1)).first() is not None
        )
        if not has_tags:
            try:
                run_job(
                    session,
                    "sync_tag_metadata",
                    partial(sync_tag_metadata, settings=settings),
                )
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("bootstrap tag sync failed: %s", exc)
        if not has_markets:
            try:
                run_job(
                    session,
                    "sync_gamma_events",
                    partial(sync_gamma_events, settings=settings),
                )
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("bootstrap gamma sync failed: %s", exc)
    finally:
        session.close()

    app.state.settings = settings
    app.state.session_factory = session_factory

    scheduler = None
    ws_client = None
    if settings.SCHEDULER_ENABLED:
        scheduler = build_scheduler(settings, session_factory)
        scheduler.start()
    if settings.CLOB_WS_ENABLED:
        ws_client = OrderbookWebsocket(settings, session_factory)
        ws_client.start()

    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown()
        if ws_client:
            ws_client.stop()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    app.include_router(router)
    return app


app = create_app()
