"""
yfinance wrapper for the Financial Overlay worker.
Returns a nullable snapshot dict — callers must tolerate None fields
because OTC/ADR tickers (NTDOY, OTGLY, etc.) often omit PE and short interest.
"""

from datetime import date

import yfinance as yf

from database.api_cache import ApiCache


def _fetch_equity_snapshot(ticker: str) -> dict:
    info = yf.Ticker(ticker).info

    price = info.get("currentPrice") or info.get("previousClose")

    pe = info.get("trailingPE") or info.get("forwardPE")

    earnings_date = None
    ts = info.get("earningsTimestamp")
    if ts:
        try:
            earnings_date = date.fromtimestamp(ts).isoformat()
        except (OSError, OverflowError, ValueError):
            pass

    short_interest = None
    raw_short = info.get("shortPercentOfFloat")
    if raw_short is not None:
        short_interest = round(raw_short * 100, 2)

    return {
        "price": price,
        "pe_ratio": pe,
        "earnings_date": earnings_date,
        "short_interest": short_interest,
    }


def get_equity_snapshot(
    ticker: str,
    cache: ApiCache | None = None,
    ttl_hours: int = 24,
) -> dict:
    key = f"snapshot:{ticker}"
    if cache:
        fresh = cache.get(key, max_age_hours=ttl_hours)
        if isinstance(fresh, dict):
            return fresh

    try:
        snapshot = _fetch_equity_snapshot(ticker)
        if cache:
            cache.set(key, snapshot)
        return snapshot
    except Exception:
        if cache:
            stale = cache.get(key)
            if isinstance(stale, dict):
                return stale
        raise
