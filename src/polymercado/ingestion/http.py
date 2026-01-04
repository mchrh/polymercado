from __future__ import annotations

import time
from typing import Any

import httpx


def fetch_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any] | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 0.5,
) -> Any:
    attempt = 0
    while True:
        attempt += 1
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            if attempt >= max_attempts:
                raise
            time.sleep(backoff_seconds * attempt)
