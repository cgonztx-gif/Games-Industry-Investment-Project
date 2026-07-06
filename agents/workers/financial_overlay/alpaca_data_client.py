from __future__ import annotations

import os
from datetime import date, timedelta

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


def get_historical_close(ticker: str, on_date: str) -> float | None:
    """
    Closing price of the nearest trading day on/before ``on_date`` from Alpaca's
    daily bars endpoint. Returns None if creds are unconfigured; raises
    requests.HTTPError on a non-2xx response (caller catches).

    A 7-calendar-day lookback window (start = on_date - 7d, end = on_date, sorted
    descending, limit 1) absorbs weekends/holidays so the single bar returned is
    the last trading day's close at or before on_date.

    Response shape (verified live against SPY): the multi-symbol bars endpoint
    nests bars per symbol -- {"bars": {"<SYMBOL>": [{"c": <close>, ...}]}} -- not
    a flat list. Parsing tolerates a flat {"bars": [...]} too and returns None
    (never crashes) if bars is missing, empty, or shaped unexpectedly.
    """
    headers = _headers()
    if not headers:
        return None

    start = (date.fromisoformat(on_date) - timedelta(days=7)).isoformat()
    resp = requests.get(
        f"{_DATA_BASE}/v2/stocks/bars",
        headers=headers,
        params={
            "symbols": ticker,
            "timeframe": "1Day",
            "start": start,
            "end": on_date,
            "feed": "iex",
            "adjustment": "raw",
            "sort": "desc",
            "limit": 1,
        },
        timeout=15,
    )
    resp.raise_for_status()

    bars = resp.json().get("bars")
    if isinstance(bars, dict):  # confirmed live shape: nested per symbol
        bars = bars.get(ticker)
    if not isinstance(bars, list) or not bars:
        return None
    close = bars[0].get("c")
    return float(close) if close is not None else None
