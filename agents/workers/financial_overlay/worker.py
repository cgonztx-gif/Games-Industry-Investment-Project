"""
Financial Overlay Worker - Phase 3

Fetches weekly equity context for each public ticker linked to active watchlist
games and writes analytical rows to equity_signals.
"""

from datetime import date

from agents.workers.financial_overlay.alpaca_data_client import get_latest_price
from agents.workers.financial_overlay.yfinance_client import get_equity_snapshot
from database.api_cache import SupabaseApiCache
from database.db_client import get_client, get_watchlist_tickers, write_equity_metrics


def run() -> dict:
    db = get_client()
    cache = SupabaseApiCache(client=db, source="yfinance")
    today = date.today().isoformat()

    tickers = get_watchlist_tickers(db)
    print(f"[financial_overlay] {len(tickers)} public tickers to fetch")

    processed: list[dict] = []
    errors: list[dict] = []

    for item in tickers:
        ticker = item["ticker"]
        studio_id = item.get("studio_id")
        try:
            snap = get_equity_snapshot(ticker, cache=cache)
            official_price = get_latest_price(ticker)
            current_price = official_price if official_price is not None else snap["price"]
            current_signal = (
                f"{item.get('tracked_games', 0)} tracked games across "
                f"{item.get('mapped_studios', 0)} mapped studios"
            )

            write_equity_metrics(
                db,
                {
                    "ticker": ticker,
                    "studio_id": studio_id,
                    "date": today,
                    "current_price": current_price,
                    "pe_ratio": snap["pe_ratio"],
                    "earnings_date": snap["earnings_date"],
                    "short_interest": snap["short_interest"],
                    "health_score": None,
                    "current_signal": current_signal,
                    "recommendation": None,
                },
            )
            processed.append({"ticker": ticker, "current_price": current_price, **snap})
            print(
                f"  {ticker}: ${current_price}  PE={snap['pe_ratio']}  "
                f"short={snap['short_interest']}%"
            )
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            print(f"  {ticker}: ERROR - {exc}")

    print(f"[financial_overlay] Complete - {len(processed)} written, {len(errors)} errors.")

    return {
        "date": today,
        "tickers_processed": len(processed),
        "error_count": len(errors),
        "snapshots": processed,
        "errors": errors,
    }
