"""
Financial Overlay Worker — Phase 3

Fetches equity snapshot (price, P/E, earnings date, short interest) for every
public ticker linked to a studio in the DB and writes to portfolio_positions_context.

Returns a structured summary dict consumed by the orchestrator.
"""

from datetime import date

from agents.workers.financial_overlay.yfinance_client import get_equity_snapshot
from database.db_client import get_client, get_watchlist_tickers, write_equity_metrics


def run() -> dict:
    db = get_client()
    today = date.today().isoformat()

    tickers = get_watchlist_tickers(db)
    print(f"[financial_overlay] {len(tickers)} public tickers to fetch")

    processed: list[dict] = []
    errors: list[dict] = []

    for item in tickers:
        ticker = item["ticker"]
        studio_id = item["studio_id"]
        try:
            snap = get_equity_snapshot(ticker)
            write_equity_metrics(db, {
                "ticker": ticker,
                "studio_id": studio_id,
                "date": today,
                "price": snap["price"],
                "pe_ratio": snap["pe_ratio"],
                "earnings_date": snap["earnings_date"],
                "short_interest": snap["short_interest"],
                "signal_score": None,
            })
            processed.append({"ticker": ticker, **snap})
            print(f"  {ticker}: ${snap['price']}  PE={snap['pe_ratio']}  short={snap['short_interest']}%")
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            print(f"  {ticker}: ERROR — {exc}")

    print(f"[financial_overlay] Complete — {len(processed)} written, {len(errors)} errors.")

    return {
        "date": today,
        "tickers_processed": len(processed),
        "error_count": len(errors),
        "snapshots": processed,
        "errors": errors,
    }
