from __future__ import annotations

import asyncio
import json
import threading
from contextlib import suppress
from decimal import Decimal
from typing import Any

import websockets
from sqlalchemy.orm import sessionmaker

from polymercado.config import AppSettings
from polymercado.ingestion.clob import upsert_orderbook
from polymercado.ingestion.universe import select_tracked_markets
from polymercado.logging import get_logger
from polymercado.models import Market
from polymercado.utils import to_decimal, utc_now

logger = get_logger(__name__)


class OrderbookWebsocket:
    def __init__(self, settings: AppSettings, session_factory: sessionmaker):
        self.settings = settings
        self.session_factory = session_factory
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._book_cache: dict[str, dict[str, Any]] = {}

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

        urls = self._ws_urls()
        if not urls:
            logger.warning("websocket disabled: no urls configured")
            return

        backoff = 1.0
        while not self._stop_event.is_set():
            for url in urls:
                if self._stop_event.is_set():
                    break
                try:
                    await self._connect_and_stream(url, token_ids)
                    backoff = 1.0
                except Exception as exc:  # pragma: no cover - network recovery
                    logger.warning("websocket error for %s: %s", url, exc)
                    continue
            if self._stop_event.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _ws_urls(self) -> list[str]:
        urls: list[str] = []
        if self.settings.CLOB_WS_URL:
            urls.append(self.settings.CLOB_WS_URL)
        if self.settings.CLOB_WS_FALLBACK_URLS:
            for raw in self.settings.CLOB_WS_FALLBACK_URLS.split(","):
                url = raw.strip()
                if url and url not in urls:
                    urls.append(url)
        return urls

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
            seen: set[str] = set()
            for market in markets:
                for token_id in market.token_ids or []:
                    if token_id in seen:
                        continue
                    seen.add(token_id)
                    tokens.append(token_id)
            return tokens
        finally:
            session.close()

    async def _connect_and_stream(self, url: str, token_ids: list[str]) -> None:
        async with websockets.connect(
            url, ping_interval=None, close_timeout=5
        ) as socket:
            current_tokens = set(token_ids)
            await self._subscribe(socket, list(current_tokens))
            ping_task = asyncio.create_task(self._ping_loop(socket))
            refresh_task = asyncio.create_task(
                self._refresh_loop(socket, current_tokens)
            )
            try:
                while not self._stop_event.is_set():
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", errors="ignore")
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue

                    event_type = data.get("event_type")
                    if event_type == "book":
                        self._handle_book(data)
                    elif event_type == "price_change":
                        self._handle_price_change(data)
            finally:
                ping_task.cancel()
                refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await ping_task
                with suppress(asyncio.CancelledError):
                    await refresh_task
                with suppress(Exception):
                    await socket.close()

    async def _subscribe(
        self, socket: websockets.WebSocketClientProtocol, token_ids: list[str]
    ) -> None:
        chunks = self._chunk_tokens(token_ids)
        if not chunks:
            return
        await socket.send(json.dumps({"assets_ids": chunks[0], "type": "market"}))
        for chunk in chunks[1:]:
            await self._send_subscribe(socket, chunk)

    async def _send_subscribe(
        self, socket: websockets.WebSocketClientProtocol, token_ids: list[str]
    ) -> None:
        for chunk in self._chunk_tokens(token_ids):
            await socket.send(
                json.dumps({"assets_ids": chunk, "operation": "subscribe"})
            )

    async def _send_unsubscribe(
        self, socket: websockets.WebSocketClientProtocol, token_ids: list[str]
    ) -> None:
        for chunk in self._chunk_tokens(token_ids):
            await socket.send(
                json.dumps({"assets_ids": chunk, "operation": "unsubscribe"})
            )

    async def _refresh_loop(
        self,
        socket: websockets.WebSocketClientProtocol,
        current_tokens: set[str],
    ) -> None:
        interval = max(self.settings.SYNC_UNIVERSE_INTERVAL_SECONDS, 1)
        while not self._stop_event.is_set():
            await asyncio.sleep(interval)
            latest = set(self._load_tokens())
            to_add = sorted(latest - current_tokens)
            to_remove = sorted(current_tokens - latest)
            if to_add:
                await self._send_subscribe(socket, to_add)
            if to_remove:
                await self._send_unsubscribe(socket, to_remove)
                for token_id in to_remove:
                    self._book_cache.pop(token_id, None)
            current_tokens.clear()
            current_tokens.update(latest)

    async def _ping_loop(self, socket: websockets.WebSocketClientProtocol) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.settings.CLOB_WS_PING_SECONDS)
            with suppress(Exception):
                await socket.send("PING")

    def _chunk_tokens(self, token_ids: list[str]) -> list[list[str]]:
        chunk_size = max(self.settings.CLOB_WS_MAX_ASSETS, 1)
        return [
            token_ids[i : i + chunk_size] for i in range(0, len(token_ids), chunk_size)
        ]

    def _handle_book(self, data: dict[str, Any]) -> None:
        book = self._normalize_book(data)
        token_id = book.get("asset_id")
        if not token_id:
            return
        if not book.get("timestamp"):
            book["timestamp"] = utc_now().isoformat()
        self._book_cache[token_id] = book
        self._persist_book(book)

    def _handle_price_change(self, data: dict[str, Any]) -> None:
        changes = data.get("price_changes") or []
        if not isinstance(changes, list):
            return
        timestamp = data.get("timestamp") or utc_now().isoformat()
        market = data.get("market")

        grouped: dict[str, list[dict[str, Any]]] = {}
        for change in changes:
            if not isinstance(change, dict):
                continue
            asset_id = change.get("asset_id")
            if not asset_id:
                continue
            grouped.setdefault(asset_id, []).append(change)

        for asset_id, asset_changes in grouped.items():
            book = self._book_cache.get(asset_id)
            if not book:
                continue
            for change in asset_changes:
                self._apply_price_change(book, change)
            if market and not book.get("market"):
                book["market"] = market
            book["timestamp"] = timestamp
            self._persist_book(book)

    def _normalize_book(self, data: dict[str, Any]) -> dict[str, Any]:
        book = dict(data)
        if "bids" not in book and "buys" in book:
            book["bids"] = book.get("buys")
        if "asks" not in book and "sells" in book:
            book["asks"] = book.get("sells")
        return book

    def _apply_price_change(self, book: dict[str, Any], change: dict[str, Any]) -> None:
        side = str(change.get("side", "")).upper()
        if side == "BUY":
            key = "bids"
        elif side == "SELL":
            key = "asks"
        else:
            return

        levels = book.get(key) or []
        updated = self._update_levels(
            levels, change.get("price"), change.get("size"), side
        )
        book[key] = updated

    def _update_levels(
        self,
        levels: list[dict[str, Any]],
        price: Any,
        size: Any,
        side: str,
    ) -> list[dict[str, Any]]:
        price_dec = to_decimal(price)
        size_dec = to_decimal(size)
        if price_dec is None or size_dec is None:
            return list(levels)

        updated: list[dict[str, Any]] = []
        found = False
        for level in levels:
            level_price = (
                to_decimal(level.get("price")) if isinstance(level, dict) else None
            )
            if level_price is None:
                continue
            if level_price == price_dec:
                found = True
                if size_dec > 0:
                    updated.append({"price": str(price_dec), "size": str(size_dec)})
            else:
                updated.append(level)

        if not found and size_dec > 0:
            updated.append({"price": str(price_dec), "size": str(size_dec)})

        reverse = side == "BUY"
        updated.sort(
            key=lambda item: to_decimal(item.get("price")) or Decimal("0"),
            reverse=reverse,
        )
        return updated

    def _persist_book(self, book: dict[str, Any]) -> None:
        if not book.get("asset_id") or not book.get("market"):
            return
        session = self.session_factory()
        try:
            upsert_orderbook(session, book)
            session.commit()
        finally:
            session.close()
