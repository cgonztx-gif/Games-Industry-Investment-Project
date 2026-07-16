from __future__ import annotations

import os

import requests

from database.db_client import get_trade_order, mark_trade_order_filled

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


def get_account_state() -> dict:
    """
    Fetch current Alpaca paper-trading account state: cash, buying power,
    portfolio value, and open positions.

    Hits GET /v2/account and GET /v2/positions (field names verified against
    Alpaca's TradeAccount/Position models: cash/buying_power/portfolio_value
    on the account, symbol/qty/market_value/avg_entry_price/current_price/
    unrealized_pl on each position -- all returned by Alpaca as decimal
    strings, coerced to float here).

    Raises the same way `_headers()` already does today (KeyError) if
    ALPACA_API_KEY / ALPACA_SECRET_KEY are unset, and raises
    requests.HTTPError on a non-2xx response. Callers (e.g. the portfolio
    manager) are responsible for catching and degrading gracefully -- this
    function does not swallow errors.
    """
    base_url = os.environ.get("ALPACA_BASE_URL") or _DEFAULT_PAPER_BASE
    headers = _headers()

    account_resp = requests.get(
        f"{base_url.rstrip('/')}/v2/account", headers=headers, timeout=20
    )
    account_resp.raise_for_status()
    account = account_resp.json()

    positions_resp = requests.get(
        f"{base_url.rstrip('/')}/v2/positions", headers=headers, timeout=20
    )
    positions_resp.raise_for_status()
    positions = positions_resp.json()

    return {
        "cash": float(account["cash"]),
        "buying_power": float(account["buying_power"]),
        "portfolio_value": float(account["portfolio_value"]),
        "positions": [
            {
                "ticker": p["symbol"],
                "qty": float(p["qty"]),
                "market_value": float(p["market_value"]),
                "avg_entry_price": float(p["avg_entry_price"]),
                "current_price": float(p["current_price"]),
                "unrealized_pnl": float(p["unrealized_pl"]),
            }
            for p in positions
        ],
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
    if trade.get("alpaca_order_id"):
        # Belt-and-suspenders against double-placement: even if the status
        # update after a placement was lost, a recorded Alpaca order id means
        # this order already reached Alpaca once.
        raise TradeNotApproved(
            f"trade_order {order_id} was already placed at Alpaca as "
            f"{trade['alpaca_order_id']!r}; refusing to place it again"
        )

    action = trade.get("action")
    if action == "hold":
        # A 'hold' is a deliberate no-trade decision the portfolio manager may
        # persist for the record; there is nothing to place at Alpaca for it.
        return {"status": "no_action", "reason": "hold orders are not placed"}
    if action not in {"buy", "sell"}:
        raise ValueError(f"trade_order {order_id} action must be buy, sell, or hold")

    base_url = os.environ.get("ALPACA_BASE_URL") or _DEFAULT_PAPER_BASE
    payload = {
        "symbol": trade["ticker"],
        "side": action,
        "type": "market",
        "time_in_force": "day",
        # Alpaca enforces client_order_id uniqueness, so even a crash between
        # the placement below and mark_trade_order_filled cannot lead to the
        # same trade_orders row being executed twice at Alpaca on a re-run.
        "client_order_id": order_id,
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
    # Flip the row out of the 'approved' pool the moment placement succeeds --
    # get_approved_trade_orders must never return it again on a later run.
    mark_trade_order_filled(db, order_id, order.get("id"))
    return order
