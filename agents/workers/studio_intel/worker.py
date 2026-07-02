"""
Studio Intel Worker - Phase 3

Fetches recent SEC EDGAR 8-K filings for every public studio in the DB, and
optionally checks configured hosted ATS job boards (Greenhouse, Lever, Ashby, or
a Playwright careers-page fallback). Writes structured rows to studio_signals.
"""

import time
from datetime import date

from agents.workers.studio_intel.ats_clients import (
    fetch_configured_jobs,
    load_ats_board_map,
    summarize_hiring_signal,
)
from agents.workers.studio_intel.edgar_client import (
    classify_8k,
    get_recent_8k_filings,
    load_cik_map,
)
from database.db_client import get_client, get_studios_with_tickers, write_studio_signal

_DAYS_BACK = 60
_REQUEST_DELAY = 0.12  # stay well under EDGAR's 10 req/sec limit


def run() -> dict:
    db = get_client()
    today = date.today().isoformat()

    studios = get_studios_with_tickers(db)
    print(f"[studio_intel] {len(studios)} public tickers to check")

    cik_map = load_cik_map()
    ats_board_map = load_ats_board_map()

    studios_checked = 0
    signals_written = 0
    skipped_no_cik = 0
    ats_checked = 0
    errors: list[dict] = []

    for item in studios:
        ticker = item["ticker"]
        studio_id = item["studio_id"]
        name = item["name"]

        cik = cik_map.get(ticker.upper())
        if cik is None:
            print(f"  {ticker} ({name}): no CIK in EDGAR - skipped")
            skipped_no_cik += 1
        else:
            try:
                time.sleep(_REQUEST_DELAY)
                filings = get_recent_8k_filings(cik, days_back=_DAYS_BACK)
                studios_checked += 1

                for filing in filings:
                    signal_type, severity = classify_8k(filing["items_raw"])
                    description = f"8-K filing - items: {filing['items_raw']}"
                    written = write_studio_signal(
                        db,
                        {
                            "studio_id": studio_id,
                            "date": filing["date"],
                            "signal_type": signal_type,
                            "description": description,
                            "severity": severity,
                            "source_url": filing["source_url"],
                        },
                    )
                    if written:
                        signals_written += 1
                        print(f"  {ticker}: [{severity}] {signal_type} on {filing['date']}")

                if not filings:
                    print(f"  {ticker}: no 8-K filings in last {_DAYS_BACK} days")

            except Exception as exc:
                errors.append({"ticker": ticker, "source": "edgar", "error": str(exc)})
                print(f"  {ticker}: EDGAR ERROR - {exc}")

        ats_config = ats_board_map.get(name) or ats_board_map.get(ticker)
        if ats_config:
            try:
                jobs = fetch_configured_jobs(ats_config)
                ats_checked += 1
                signal = summarize_hiring_signal(jobs)
                if signal:
                    signal_type, description = signal
                    written = write_studio_signal(
                        db,
                        {
                            "studio_id": studio_id,
                            "date": today,
                            "signal_type": signal_type,
                            "description": description,
                            "severity": "medium",
                            "source_url": jobs[0].get("url") if jobs else None,
                        },
                    )
                    if written:
                        signals_written += 1
                        print(f"  {ticker}: [medium] {signal_type} from ATS board")
            except Exception as exc:
                errors.append({"ticker": ticker, "source": "ats", "error": str(exc)})
                print(f"  {ticker}: ATS ERROR - {exc}")

    print(
        f"[studio_intel] Complete - {studios_checked} EDGAR checks, "
        f"{ats_checked} ATS boards checked, {signals_written} signals written, "
        f"{skipped_no_cik} skipped (no CIK), {len(errors)} errors."
    )

    return {
        "date": today,
        "studios_checked": studios_checked,
        "ats_boards_checked": ats_checked,
        "signals_written": signals_written,
        "skipped_no_cik": skipped_no_cik,
        "error_count": len(errors),
        "errors": errors,
    }
