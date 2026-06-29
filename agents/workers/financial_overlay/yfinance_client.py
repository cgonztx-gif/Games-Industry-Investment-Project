"""
yfinance wrapper for the Financial Overlay worker.
Returns a nullable snapshot dict — callers must tolerate None fields
because OTC/ADR tickers (NTDOY, OTGLY, etc.) often omit PE and short interest.
"""

from datetime import date

import yfinance as yf


def get_equity_snapshot(ticker: str) -> dict:
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
