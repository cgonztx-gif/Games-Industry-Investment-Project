"""
yfinance wrapper for the Financial Overlay worker.
Returns a nullable snapshot dict — callers must tolerate None fields
because OTC/ADR tickers (NTDOY, OTGLY, etc.) often omit PE and short interest.
"""

from datetime import date, timedelta

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


def get_historical_close(
    ticker: str,
    on_date: str,
    cache: ApiCache | None = None,
) -> float | None:
    """
    Closing price of the nearest trading day on/before ``on_date`` from yfinance.

    Fallback for the Alpaca-bars leg of the Returns Tracker's SPY benchmark lookup.
    Follows get_equity_snapshot's try-primary / cache-on-success / stale-fallback
    shape, but reads the cache with max_age_hours=None (cache forever): a past
    trading day's close never changes, so any previously stored value is valid
    indefinitely. Returns None (rather than raising, per its nullable signature)
    when yfinance yields no data or errors and there is no cached value.
    """
    key = f"close:{ticker}:{on_date}"
    if cache:
        cached = cache.get(key, max_age_hours=None)
        if isinstance(cached, dict) and cached.get("close") is not None:
            return float(cached["close"])

    try:
        target = date.fromisoformat(on_date)
        start = (target - timedelta(days=7)).isoformat()
        end = (target + timedelta(days=1)).isoformat()
        hist = yf.Ticker(ticker).history(start=start, end=end)
        if hist is None or hist.empty:
            return None
        close = float(hist["Close"].iloc[-1])
        if cache:
            cache.set(key, {"close": close})
        return close
    except Exception:
        if cache:
            stale = cache.get(key)
            if isinstance(stale, dict) and stale.get("close") is not None:
                return float(stale["close"])
        return None
