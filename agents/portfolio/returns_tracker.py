"""
Returns Tracker - Phase 5

A lightweight, no-LLM tracker that runs after each weekly execution: it fetches
current Alpaca paper-trading account state and writes one portfolio_snapshots row
capturing portfolio value, cash balance, cumulative return since inception, and a
weekly-vs-SPY benchmark comparison to Supabase.

Since-inception semantics (design decision, not week-over-week):
- ``total_return_pct`` and ``benchmark_return_pct`` are BOTH cumulative percent
  returns measured against the earliest existing portfolio_snapshots row (the
  "inception" baseline), so the two columns stay apples-to-apples on every row and
  match the position-sizing-and-risk skill's "cumulative ... since inception"
  framing.
- On the very first run (table empty) this run's own value becomes the baseline:
  both ``_pct`` fields are 0.0 and NO SPY lookup is performed at all (there is
  nothing to compare against yet -- a real short-circuit).

SPY benchmark lookup (only when a baseline row exists), each leg independent:
- baseline-date SPY close: Alpaca daily bars -> yfinance history fallback
  (cached forever, since a past trading day's close is immutable).
- current SPY price: Alpaca latest trade -> yfinance snapshot ["price"] fallback.
  If either leg is unavailable on both tiers, ``benchmark_return_pct`` is written
  as None (a plain nullable numeric column -- not a textual "UNAVAILABLE" marker,
  which is specific to the LLM-facing MCP tool text elsewhere).

Degradation:
- get_account_state() failing entirely -> nothing meaningful to snapshot -> log and
  return None (clean skip, mirroring manager.py's "no briefing -> None").
- A stored baseline total_value of 0 -> ``total_return_pct`` is None (no
  ZeroDivisionError).

Dependency injection: every DB/API touch point is a ``<verb>_fn=`` keyword
parameter defaulting to the real implementation (matching manager.py /
execution_agent.py), so tests drive the full flow with plain fakes and zero live
network/DB/API calls.

Known gap (out of scope): the schema's ``positions`` table (per-position P&L /
holding period) has no readers or writers anywhere in the codebase and no
holding-period column, so the brief's per-position P&L view is not produced here.
"""

from __future__ import annotations

from datetime import date

from agents.portfolio.alpaca_trading_client import get_account_state
from agents.workers.financial_overlay.alpaca_data_client import (
    get_historical_close as get_historical_close_alpaca,
)
from agents.workers.financial_overlay.alpaca_data_client import get_latest_price
from agents.workers.financial_overlay.yfinance_client import get_equity_snapshot
from agents.workers.financial_overlay.yfinance_client import (
    get_historical_close as get_historical_close_yf,
)
from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_earliest_portfolio_snapshot,
    write_portfolio_snapshot,
)

_BENCHMARK = "SPY"


def run(
    db=None,
    run_date: str | None = None,
    get_account_state_fn=get_account_state,
    get_earliest_snapshot_fn=get_earliest_portfolio_snapshot,
    get_latest_spy_price_fn=get_latest_price,
    get_spy_equity_snapshot_fn=get_equity_snapshot,
    get_alpaca_spy_close_fn=get_historical_close_alpaca,
    get_yfinance_spy_close_fn=get_historical_close_yf,
    write_snapshot_fn=write_portfolio_snapshot,
    cache=None,
) -> dict | None:
    """
    Fetch Alpaca portfolio state, compute since-inception returns vs. SPY, and
    write one portfolio_snapshots row. Returns the written row plus ``baseline_date``,
    or None if the account fetch fails (nothing to snapshot).
    """
    active_db = db if db is not None else get_client()
    as_of = run_date or date.today().isoformat()
    active_cache = cache if cache is not None else SupabaseApiCache(active_db, source="yfinance")

    try:
        account = get_account_state_fn()
    except Exception as exc:  # noqa: BLE001 -- no state means nothing to snapshot
        print(f"[returns tracker] account state fetch failed: {exc}; skipping snapshot")
        return None

    total_value = account["portfolio_value"]
    cash = account["cash"]

    baseline_row = get_earliest_snapshot_fn(active_db)

    if baseline_row is None:
        # First run ever: this value becomes the inception baseline. Nothing to
        # compare against, so short-circuit the entire SPY lookup.
        total_return_pct = 0.0
        benchmark_return_pct = 0.0
        baseline_date = as_of
    else:
        baseline_value = float(baseline_row["total_value"])
        baseline_date = baseline_row["date"]
        total_return_pct = (
            round((total_value - baseline_value) / baseline_value * 100, 3)
            if baseline_value
            else None
        )
        benchmark_return_pct = _spy_benchmark_return(
            baseline_date=baseline_date,
            cache=active_cache,
            get_alpaca_spy_close_fn=get_alpaca_spy_close_fn,
            get_yfinance_spy_close_fn=get_yfinance_spy_close_fn,
            get_latest_spy_price_fn=get_latest_spy_price_fn,
            get_spy_equity_snapshot_fn=get_spy_equity_snapshot_fn,
        )

    row = {
        "date": as_of,
        "total_value": round(total_value, 2),
        "cash": round(cash, 2),
        "total_return_pct": total_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
    }
    write_snapshot_fn(active_db, row)

    print(
        f"[returns tracker] snapshot {as_of}: value=${row['total_value']} "
        f"cash=${row['cash']} return={total_return_pct}% "
        f"benchmark={benchmark_return_pct}% (baseline {baseline_date})"
    )

    return {**row, "baseline_date": baseline_date}


def _spy_benchmark_return(
    *,
    baseline_date: str,
    cache,
    get_alpaca_spy_close_fn,
    get_yfinance_spy_close_fn,
    get_latest_spy_price_fn,
    get_spy_equity_snapshot_fn,
) -> float | None:
    """
    Cumulative SPY return from ``baseline_date`` to now, or None if either the
    baseline-date close or the current price is unavailable on both tiers. Each
    leg is independently try/excepted and logged.
    """
    baseline_price = _spy_baseline_price(
        baseline_date,
        cache,
        get_alpaca_spy_close_fn,
        get_yfinance_spy_close_fn,
    )
    current_price = _spy_current_price(
        cache,
        get_latest_spy_price_fn,
        get_spy_equity_snapshot_fn,
    )

    if not baseline_price or current_price is None:
        return None
    return round((current_price - baseline_price) / baseline_price * 100, 3)


def _spy_baseline_price(
    baseline_date, cache, get_alpaca_spy_close_fn, get_yfinance_spy_close_fn
) -> float | None:
    try:
        value = get_alpaca_spy_close_fn(_BENCHMARK, baseline_date)
        if value is not None:
            return value
    except Exception as exc:  # noqa: BLE001 -- fall through to yfinance
        print(f"[returns tracker] Alpaca SPY baseline close failed: {exc}; trying yfinance")

    try:
        return get_yfinance_spy_close_fn(_BENCHMARK, baseline_date, cache)
    except Exception as exc:  # noqa: BLE001
        print(f"[returns tracker] yfinance SPY baseline close failed: {exc}")
        return None


def _spy_current_price(
    cache, get_latest_spy_price_fn, get_spy_equity_snapshot_fn
) -> float | None:
    try:
        value = get_latest_spy_price_fn(_BENCHMARK)
        if value is not None:
            return value
    except Exception as exc:  # noqa: BLE001 -- fall through to yfinance
        print(f"[returns tracker] Alpaca SPY latest price failed: {exc}; trying yfinance")

    try:
        snap = get_spy_equity_snapshot_fn(_BENCHMARK, cache=cache)
        return snap.get("price") if isinstance(snap, dict) else None
    except Exception as exc:  # noqa: BLE001
        print(f"[returns tracker] yfinance SPY snapshot failed: {exc}")
        return None
