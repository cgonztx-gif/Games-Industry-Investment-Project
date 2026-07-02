from __future__ import annotations

import os

import requests

_DATA_BASE = "https://data.alpaca.markets"


def _headers() -> dict | None:
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def get_latest_price(ticker: str) -> float | None:
    """Latest trade price from Alpaca Market Data. Returns None if unconfigured."""
    headers = _headers()
    if not headers:
        return None

    resp = requests.get(
        f"{_DATA_BASE}/v2/stocks/{ticker}/trades/latest",
        headers=headers,
        params={"feed": "iex"},
        timeout=15,
    )
    resp.raise_for_status()
    trade = (resp.json().get("trade") or {})
    price = trade.get("p")
    return float(price) if price is not None else None
