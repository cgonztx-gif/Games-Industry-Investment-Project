"""
Unit tests for agents/portfolio/returns_tracker.py.

No live network / Alpaca / yfinance / Supabase calls. run() exposes every DB/API
touch point as an injectable ``<verb>_fn``, so each test drives the full flow with
plain recording fakes. Assertion-raising fakes prove the first-run short-circuit
never reaches the SPY-close lookups, and that the account-failure path never writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.portfolio import returns_tracker

_FAKE_DB = "fake-db-sentinel"
_FAKE_CACHE = "fake-cache-sentinel"


class _Recorder:
    """Records every (db, row) passed to the snapshot writer."""

    def __init__(self):
        self.rows = []

    def __call__(self, db, row):
        self.rows.append(row)


def _account(portfolio_value, cash=1000.0):
    return lambda: {
        "portfolio_value": portfolio_value,
        "cash": cash,
        "buying_power": portfolio_value,
        "positions": [],
    }


def _boom(*_args, **_kwargs):
    raise AssertionError("this fake must never be called")


def _run(**overrides):
    """run() with inert defaults; each test overrides the seams it exercises."""
    kwargs = dict(
        db=_FAKE_DB,
        run_date="2026-07-06",
        cache=_FAKE_CACHE,
        get_account_state_fn=_account(100000.0),
        get_earliest_snapshot_fn=lambda db: None,
        get_latest_spy_price_fn=lambda ticker: None,
        get_spy_equity_snapshot_fn=lambda ticker, cache=None: {"price": None},
        get_alpaca_spy_close_fn=lambda ticker, on_date: None,
        get_yfinance_spy_close_fn=lambda ticker, on_date, cache=None: None,
        write_snapshot_fn=_Recorder(),
    )
    kwargs.update(overrides)
    return returns_tracker.run(**kwargs), kwargs["write_snapshot_fn"]


def test_account_fetch_failure_returns_none_and_never_writes():
    writer = _Recorder()
    result = returns_tracker.run(
        db=_FAKE_DB,
        cache=_FAKE_CACHE,
        get_account_state_fn=_boom,
        get_earliest_snapshot_fn=lambda db: None,
        write_snapshot_fn=writer,
    )
    assert result is None
    assert writer.rows == []


def test_first_run_short_circuits_spy_lookup():
    result, writer = _run(
        get_account_state_fn=_account(100000.0),
        get_earliest_snapshot_fn=lambda db: None,  # empty table
        # Prove the SPY-close legs are never touched on the first run:
        get_alpaca_spy_close_fn=_boom,
        get_yfinance_spy_close_fn=_boom,
    )
    assert len(writer.rows) == 1
    row = writer.rows[0]
    assert row["total_return_pct"] == 0.0
    assert row["benchmark_return_pct"] == 0.0
    assert result["total_return_pct"] == 0.0
    assert result["benchmark_return_pct"] == 0.0
    assert result["baseline_date"] == "2026-07-06"


def test_since_inception_returns_computed_against_baseline():
    result, writer = _run(
        get_account_state_fn=_account(110000.0, cash=5000.0),
        get_earliest_snapshot_fn=lambda db: {"date": "2026-06-01", "total_value": 100000.0},
        get_alpaca_spy_close_fn=lambda ticker, on_date: 500.0,
        get_latest_spy_price_fn=lambda ticker: 525.0,
    )
    row = writer.rows[0]
    assert row["total_value"] == 110000.0
    assert row["cash"] == 5000.0
    assert row["total_return_pct"] == 10.0
    assert row["benchmark_return_pct"] == 5.0
    assert result["total_return_pct"] == 10.0
    assert result["benchmark_return_pct"] == 5.0
    assert result["baseline_date"] == "2026-06-01"


def test_spy_unavailable_both_tiers_still_writes_row_with_null_benchmark():
    result, writer = _run(
        get_account_state_fn=_account(110000.0),
        get_earliest_snapshot_fn=lambda db: {"date": "2026-06-01", "total_value": 100000.0},
        # Both baseline-close tiers fail (one raises, one returns None);
        # current-price tiers also yield nothing.
        get_alpaca_spy_close_fn=lambda ticker, on_date: (_ for _ in ()).throw(RuntimeError("boom")),
        get_yfinance_spy_close_fn=lambda ticker, on_date, cache=None: None,
        get_latest_spy_price_fn=lambda ticker: None,
        get_spy_equity_snapshot_fn=lambda ticker, cache=None: {"price": None},
    )
    assert len(writer.rows) == 1
    row = writer.rows[0]
    assert row["total_value"] == 110000.0
    assert row["total_return_pct"] == 10.0
    assert row["benchmark_return_pct"] is None
    assert result["benchmark_return_pct"] is None


def test_baseline_close_falls_back_from_alpaca_to_yfinance():
    _result, writer = _run(
        get_account_state_fn=_account(110000.0),
        get_earliest_snapshot_fn=lambda db: {"date": "2026-06-01", "total_value": 100000.0},
        get_alpaca_spy_close_fn=lambda ticker, on_date: (_ for _ in ()).throw(RuntimeError("alpaca down")),
        get_yfinance_spy_close_fn=lambda ticker, on_date, cache=None: 500.0,  # fallback value
        get_latest_spy_price_fn=lambda ticker: 525.0,
    )
    # 500 -> 525 = +5.0%, sourced entirely from the yfinance fallback leg.
    assert writer.rows[0]["benchmark_return_pct"] == 5.0


def test_same_run_date_twice_takes_the_upsert_path_both_times():
    writer = _Recorder()
    common = dict(
        db=_FAKE_DB,
        run_date="2026-07-06",
        cache=_FAKE_CACHE,
        get_account_state_fn=_account(100000.0),
        get_earliest_snapshot_fn=lambda db: None,
        write_snapshot_fn=writer,
    )
    returns_tracker.run(**common)
    returns_tracker.run(**common)
    assert len(writer.rows) == 2
    assert writer.rows[0]["date"] == "2026-07-06"
    assert writer.rows[1]["date"] == "2026-07-06"


def test_zero_baseline_value_yields_null_return_without_crashing():
    _result, writer = _run(
        get_account_state_fn=_account(110000.0),
        get_earliest_snapshot_fn=lambda db: {"date": "2026-06-01", "total_value": 0},
        get_alpaca_spy_close_fn=lambda ticker, on_date: 500.0,
        get_latest_spy_price_fn=lambda ticker: 525.0,
    )
    assert writer.rows[0]["total_return_pct"] is None
