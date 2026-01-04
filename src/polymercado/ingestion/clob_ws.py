from __future__ import annotations

import asyncio
import json
import threading

import websockets
from sqlalchemy.orm import sessionmaker

from polymercado.config import AppSettings
from polymercado.ingestion.clob import upsert_orderbook
from polymercado.ingestion.universe import select_tracked_markets
from polymercado.models import Market

WSS_BASE = "wss://ws-subscriptions-clob.polymarket.com"


class OrderbookWebsocket:
    def __init__(self, settings: AppSettings, session_factory: sessionmaker):
        self.settings = settings
        self.session_factory = session_factory
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        asyncio.run(self._run_loop())

    async def _run_loop(self) -> None:
        token_ids = self._load_tokens()
        if not token_ids:
            return

        urls = [f"{WSS_BASE}/ws/market", f"{WSS_BASE}/ws/"]
        for url in urls:
            try:
                await self._connect_and_stream(url, token_ids)
                return
            except Exception:
                continue

    def _load_tokens(self) -> list[str]:
        session = self.session_factory()
        try:
            condition_ids = select_tracked_markets(session, self.settings)
            if not condition_ids:
                return []
            markets = (
                session.query(Market)
                .filter(Market.condition_id.in_(condition_ids))
                .all()
            )
            tokens: list[str] = []
            for market in markets:
                tokens.extend(market.token_ids or [])
            return tokens
        finally:
            session.close()

    async def _connect_and_stream(self, url: str, token_ids: list[str]) -> None:
        async with websockets.connect(url) as socket:
            payload = {"assets_ids": token_ids, "type": "market"}
            await socket.send(json.dumps(payload))

            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        socket.recv(), timeout=self.settings.CLOB_WS_PING_SECONDS
                    )
                except asyncio.TimeoutError:
                    await socket.send("PING")
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data.get("event_type") == "book":
                    self._handle_book(data)

    def _handle_book(self, book: dict) -> None:
        session = self.session_factory()
        try:
            upsert_orderbook(session, book)
            session.commit()
        finally:
            session.close()
