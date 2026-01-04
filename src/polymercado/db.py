from __future__ import annotations

from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from polymercado.config import AppSettings
from polymercado.models import Base


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine_from_url(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite)
    return engine


@lru_cache
def get_engine(database_url: str) -> Engine:
    return create_engine_from_url(database_url)


def init_db(settings: AppSettings) -> None:
    engine = get_engine(settings.DATABASE_URL)
    Base.metadata.create_all(engine)


@lru_cache
def get_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = get_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session(settings: AppSettings) -> Iterator[Session]:
    session_factory = get_session_factory(settings.DATABASE_URL)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
