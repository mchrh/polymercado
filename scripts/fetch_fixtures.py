from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets
from websockets.exceptions import WebSocketException

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
WSS_BASE = "wss://ws-subscriptions-clob.polymarket.com"


def parse_jsonish_array(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item is not None]
        if stripped.startswith("[") and stripped.endswith("]"):
            inner = stripped[1:-1].strip()
            if not inner:
                return []
            parts = [part.strip() for part in inner.split(",")]
            return [part.strip("\"'") for part in parts if part.strip("\"'")]
        return [stripped]
    return [str(value)]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")


def get_json(
    client: httpx.Client, url: str, params: dict[str, Any] | None = None
) -> Any:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def fetch_book_for_tokens(
    client: httpx.Client, token_ids: list[str]
) -> tuple[str, dict[str, Any]]:
    for token_id in token_ids:
        response = client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        if response.status_code == 404:
            continue
        response.raise_for_status()
        return token_id, response.json()
    raise RuntimeError("No CLOB orderbook found for token candidates.")


async def fetch_ws_book(
    asset_id: str, timeout_seconds: float
) -> tuple[str, dict[str, Any]]:
    ws_urls = [
        f"{WSS_BASE}/ws/market",
        f"{WSS_BASE}/ws/market/",
        f"{WSS_BASE}/ws/",
        f"{WSS_BASE}/ws",
    ]
    last_error: Exception | None = None
    for ws_url in ws_urls:
        try:
            data = await fetch_ws_book_from_url(ws_url, asset_id, timeout_seconds)
            return ws_url, data
        except (TimeoutError, WebSocketException) as exc:
            last_error = exc
            continue
    raise TimeoutError("Timed out waiting for websocket book message.") from last_error


async def fetch_ws_book_from_url(
    ws_url: str, asset_id: str, timeout_seconds: float
) -> dict[str, Any]:
    payloads = [
        {"assets_ids": [asset_id], "type": "market"},
        {"assets_ids": [asset_id], "type": "MARKET"},
        {"assets_ids": [asset_id], "operation": "subscribe"},
    ]
    deadline = time.monotonic() + timeout_seconds

    async with websockets.connect(ws_url) as socket:
        for payload in payloads:
            await socket.send(json.dumps(payload))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for websocket book message.")
            message = await asyncio.wait_for(socket.recv(), timeout=remaining)
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("event_type") == "book":
                return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Polymarket API fixtures.")
    parser.add_argument(
        "--output-dir",
        default="tests/fixtures/polymarket",
        help="Directory to write fixture JSON files.",
    )
    parser.add_argument(
        "--events-limit",
        type=int,
        default=10,
        help="Number of Gamma events to scan for a market with a live orderbook.",
    )
    parser.add_argument(
        "--ws-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for a websocket book message.",
    )
    parser.add_argument(
        "--require-ws",
        action="store_true",
        help="Fail if a websocket book snapshot cannot be fetched.",
    )
    parser.add_argument(
        "--allow-trades-fallback",
        action="store_true",
        help="Allow trades to fall back to an unfiltered query if none match the market.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    out_dir = Path(args.output_dir)

    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gamma": {},
        "data_api": {},
        "clob": {},
        "notes": [],
    }

    with httpx.Client(timeout=15.0) as client:
        events_params = {
            "active": "true",
            "closed": "false",
            "limit": args.events_limit,
            "offset": 0,
            "order": "id",
            "ascending": "false",
        }
        events = get_json(client, f"{GAMMA_BASE}/events", params=events_params)
        if not events:
            raise RuntimeError("Gamma /events returned no data.")
        write_json(out_dir / "gamma_events.json", events)

        event = None
        market = None
        condition_id = None
        token_ids: list[str] = []
        book = None
        book_token_id = None

        for event_candidate in events:
            for market_candidate in event_candidate.get("markets") or []:
                candidate_condition_id = market_candidate.get("conditionId")
                candidate_token_ids = parse_jsonish_array(
                    market_candidate.get("clobTokenIds")
                )
                if not candidate_condition_id or not candidate_token_ids:
                    continue
                try:
                    book_token_id, book = fetch_book_for_tokens(
                        client, candidate_token_ids
                    )
                except RuntimeError:
                    continue
                event = event_candidate
                market = market_candidate
                condition_id = candidate_condition_id
                token_ids = candidate_token_ids
                break
            if book:
                break

        if not book or not event or not market or not condition_id or not token_ids:
            raise RuntimeError(
                "No Gamma market with a live CLOB orderbook found in events response."
            )
        if not book_token_id:
            raise RuntimeError("Selected market missing book token id.")

        manifest["gamma"] = {
            "events_params": events_params,
            "event_id": event.get("id"),
            "market_id": market.get("id"),
            "condition_id": condition_id,
            "token_ids": token_ids,
        }

        oi_params = {"market": condition_id}
        oi = get_json(client, f"{DATA_BASE}/oi", params=oi_params)
        write_json(out_dir / "data_oi.json", oi)
        manifest["data_api"]["oi_params"] = oi_params

        trades_params = {
            "limit": 5,
            "offset": 0,
            "takerOnly": "true",
            "filterType": "CASH",
            "filterAmount": 10000,
            "market": condition_id,
        }
        trades = get_json(client, f"{DATA_BASE}/trades", params=trades_params)
        if not trades and args.allow_trades_fallback:
            trades_params.pop("market")
            trades = get_json(client, f"{DATA_BASE}/trades", params=trades_params)
            manifest["notes"].append("trades_fallback_without_market_filter")
        elif not trades:
            manifest["notes"].append("trades_empty_for_market_filter")
        write_json(out_dir / "data_trades.json", trades)
        manifest["data_api"]["trades_params"] = trades_params

        positions = []
        wallet = None
        if trades:
            wallet = trades[0].get("proxyWallet")
        if wallet:
            positions_params = {"user": wallet, "limit": 5, "offset": 0}
            positions = get_json(
                client, f"{DATA_BASE}/positions", params=positions_params
            )
            manifest["data_api"]["positions_params"] = positions_params
        else:
            manifest["notes"].append("positions_skipped_missing_proxy_wallet")
        write_json(out_dir / "data_positions.json", positions)

        write_json(out_dir / "clob_book.json", book)
        manifest["clob"]["book_token_id"] = book_token_id
        manifest["clob"]["book_token_candidates"] = token_ids

    ws_fixture_path = out_dir / "clob_ws_book.json"
    ws_url = None
    ws_book = None
    ws_error = None
    try:
        ws_url, ws_book = asyncio.run(fetch_ws_book(book_token_id, args.ws_timeout))
    except Exception as exc:  # pragma: no cover - best-effort network path
        ws_error = str(exc)

    if ws_error:
        manifest["notes"].append("ws_book_fetch_failed")
        manifest["clob"]["ws"] = {
            "asset_id": book_token_id,
            "timeout_seconds": args.ws_timeout,
            "error": ws_error,
        }
        if args.require_ws:
            raise RuntimeError(ws_error)
        if not ws_fixture_path.exists():
            write_json(
                ws_fixture_path,
                {"error": ws_error, "asset_id": book_token_id},
            )
    else:
        write_json(ws_fixture_path, ws_book)
        manifest["clob"]["ws"] = {
            "url": ws_url,
            "asset_id": book_token_id,
            "timeout_seconds": args.ws_timeout,
        }

    write_json(out_dir / "fixtures_manifest.json", manifest)


if __name__ == "__main__":
    main()
