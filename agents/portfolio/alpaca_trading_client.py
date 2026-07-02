from __future__ import annotations

import os

import requests

from database.db_client import attach_alpaca_order_id, get_trade_order

_DEFAULT_PAPER_BASE = "https://paper-api.alpaca.markets"


class TradeNotApproved(Exception):
    """Raised when an order-placement call is attempted without approval."""


def _headers() -> dict:
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
    }


def place_approved_order(db, order_id: str) -> dict:
    """
    Place one Alpaca paper order after re-reading Supabase approval status.

    The approval guard lives here, inside the order-placement tool, so it cannot
    be bypassed by orchestration or prompt changes.
    """
    trade = get_trade_order(db, order_id)
    if trade is None:
        raise TradeNotApproved(f"trade_order {order_id} not found")
    if trade.get("status") != "approved":
        raise TradeNotApproved(
            f"trade_order {order_id} has status {trade.get('status')!r}; expected 'approved'"
        )

    action = trade.get("action")
    if action not in {"buy", "sell"}:
        raise ValueError(f"trade_order {order_id} action must be buy or sell")

    base_url = os.environ.get("ALPACA_BASE_URL") or _DEFAULT_PAPER_BASE
    payload = {
        "symbol": trade["ticker"],
        "side": action,
        "type": "market",
        "time_in_force": "day",
        "notional": str(trade["size_usd"]),
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/v2/orders",
        headers=_headers(),
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    order = resp.json()
    alpaca_order_id = order.get("id")
    if alpaca_order_id:
        attach_alpaca_order_id(db, order_id, alpaca_order_id)
    return order
