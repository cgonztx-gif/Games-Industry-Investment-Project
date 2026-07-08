"""
Financial Overlay Worker - Phase 3

Fetches weekly equity context for each public ticker linked to active watchlist
games and writes analytical rows to equity_signals, including a deterministic
composite health_score (see health_score.py for the formula and its limits).
"""

from datetime import date, timedelta

from agents.workers.financial_overlay.alpaca_data_client import get_latest_price
from agents.workers.financial_overlay.health_score import LOOKBACK_DAYS, compute_health_score
from agents.workers.financial_overlay.yfinance_client import get_equity_snapshot
from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_recent_community_sentiment_for_games,
    get_recent_patch_events_for_games,
    get_recent_player_metrics_for_games,
    get_recent_studio_signals_for_studios,
    get_watchlist_tickers,
    write_equity_metrics,
)


def _health_score_for_ticker(db, item: dict, today: date) -> tuple[float | None, dict]:
    game_ids = item.get("game_ids") or []
    studio_ids = item.get("studio_ids") or []

    sentiment_rows = get_recent_community_sentiment_for_games(
        db, game_ids, (today - timedelta(days=LOOKBACK_DAYS["sentiment"])).isoformat()
    )
    patch_event_rows = get_recent_patch_events_for_games(
        db, game_ids, (today - timedelta(days=LOOKBACK_DAYS["patch_events"])).isoformat()
    )
    player_metrics_rows = get_recent_player_metrics_for_games(
        db, game_ids, (today - timedelta(days=LOOKBACK_DAYS["player_metrics"])).isoformat()
    )
    studio_signal_rows = get_recent_studio_signals_for_studios(
        db, studio_ids, (today - timedelta(days=LOOKBACK_DAYS["studio_signals"])).isoformat()
    )

    return compute_health_score(
        sentiment_rows=sentiment_rows,
        patch_event_rows=patch_event_rows,
        player_metrics_rows=player_metrics_rows,
        studio_ids=studio_ids,
        studio_signal_rows=studio_signal_rows,
    )


def _current_signal_text(item: dict, health_score, components: dict) -> str:
    base = (
        f"{item.get('tracked_games', 0)} tracked games across "
        f"{item.get('mapped_studios', 0)} mapped studios"
    )
    if health_score is None:
        return f"{base}; health_score unavailable (no worker coverage yet for mapped games)"
    breakdown = ", ".join(f"{name} {score}" for name, score in components.items())
    return f"{base}; health_score {health_score} ({breakdown})"


def run() -> dict:
    db = get_client()
    cache = SupabaseApiCache(client=db, source="yfinance")
    today = date.today()
    today_iso = today.isoformat()

    tickers = get_watchlist_tickers(db)
    print(f"[financial_overlay] {len(tickers)} public tickers to fetch")

    processed: list[dict] = []
    errors: list[dict] = []

    for item in tickers:
        ticker = item["ticker"]
        studio_id = item.get("studio_id")
        try:
            snap = get_equity_snapshot(ticker, cache=cache)
            try:
                official_price = get_latest_price(ticker)
            except Exception:
                official_price = None
            current_price = official_price if official_price is not None else snap["price"]
            health_score, components = _health_score_for_ticker(db, item, today)
            current_signal = _current_signal_text(item, health_score, components)

            write_equity_metrics(
                db,
                {
                    "ticker": ticker,
                    "studio_id": studio_id,
                    "date": today_iso,
                    "current_price": current_price,
                    "pe_ratio": snap["pe_ratio"],
                    "earnings_date": snap["earnings_date"],
                    "short_interest": snap["short_interest"],
                    "health_score": health_score,
                    "current_signal": current_signal,
                    "recommendation": None,
                },
            )
            processed.append(
                {
                    "ticker": ticker,
                    "current_price": current_price,
                    "health_score": health_score,
                    **snap,
                }
            )
            print(
                f"  {ticker}: ${current_price}  PE={snap['pe_ratio']}  "
                f"short={snap['short_interest']}%  health_score={health_score}"
            )
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            print(f"  {ticker}: ERROR - {exc}")

    print(f"[financial_overlay] Complete - {len(processed)} written, {len(errors)} errors.")

    return {
        "date": today_iso,
        "tickers_processed": len(processed),
        "error_count": len(errors),
        "snapshots": processed,
        "errors": errors,
    }
