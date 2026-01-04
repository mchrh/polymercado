# Polymarket API fixtures

These JSON files are captured from live Polymarket public APIs for parser tests.

Refresh fixtures:

```
uv sync --extra dev
uv run python scripts/fetch_fixtures.py
```

Notes:
- Uses public endpoints only.
- WebSocket sample uses the market channel and may need a retry if no book arrives quickly.
- Use `--require-ws` to fail the run if a websocket snapshot cannot be fetched.
- Use `--allow-trades-fallback` to pull unfiltered trades when the market-specific query is empty.
