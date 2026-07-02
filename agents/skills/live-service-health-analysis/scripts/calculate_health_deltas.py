"""Compute deterministic live-service deltas from metric snapshots.

Input JSON is a list of rows with at least:
  {"date": "YYYY-MM-DD", "concurrent_players": 123, "review_count": 456}

The script prints a compact JSON summary for the latest row.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 2)


def _rolling_average(values: list[float], window: int) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    window_values = usable[-window:]
    return round(sum(window_values) / len(window_values), 2)


def _classify_delta(ccu_wow_pct: float | None) -> str:
    if ccu_wow_pct is None:
        return "insufficient_history"
    if ccu_wow_pct <= -30:
        return "severe_decline"
    if ccu_wow_pct <= -15:
        return "decline"
    if ccu_wow_pct >= 25:
        return "spike"
    if ccu_wow_pct >= 10:
        return "growth"
    return "stable"


def summarize(rows: list[dict[str, Any]], window: int = 4) -> dict[str, Any]:
    if not rows:
        return {"trend_status": "insufficient_history", "reason": "no rows"}

    ordered = sorted(rows, key=lambda row: date.fromisoformat(row["date"]))
    latest = ordered[-1]
    previous = ordered[-2] if len(ordered) >= 2 else None

    latest_ccu = _number(latest.get("concurrent_players"))
    previous_ccu = _number(previous.get("concurrent_players")) if previous else None
    ccu_values = [
        _number(row.get("concurrent_players"))
        for row in ordered
        if _number(row.get("concurrent_players")) is not None
    ]

    latest_reviews = _number(latest.get("review_count"))
    previous_reviews = _number(previous.get("review_count")) if previous else None
    review_count_delta = (
        int(latest_reviews - previous_reviews)
        if latest_reviews is not None and previous_reviews is not None
        else None
    )

    ccu_wow_pct = _pct_change(latest_ccu, previous_ccu)
    ccu_rolling_avg = _rolling_average(ccu_values, window)

    return {
        "date": latest["date"],
        "observations": len(ordered),
        "ccu": int(latest_ccu) if latest_ccu is not None else None,
        "previous_ccu": int(previous_ccu) if previous_ccu is not None else None,
        "ccu_wow_pct": ccu_wow_pct,
        "ccu_rolling_avg": ccu_rolling_avg,
        "ccu_vs_rolling_pct": _pct_change(latest_ccu, ccu_rolling_avg),
        "review_count": int(latest_reviews) if latest_reviews is not None else None,
        "review_count_delta": review_count_delta,
        "review_velocity": latest.get("review_velocity"),
        "trend_status": _classify_delta(ccu_wow_pct),
    }


def _read_input(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("input must be a JSON list of metric rows")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="JSON file path, or '-' for stdin")
    parser.add_argument("--window", type=int, default=4, help="rolling average window")
    args = parser.parse_args()

    result = summarize(_read_input(args.input), window=args.window)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
