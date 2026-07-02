"""
Execution Agent - Phase 5

Thin runner for approved Alpaca paper-trading orders. The safety check is inside
alpaca_trading_client.place_approved_order().
"""

from database.db_client import get_approved_trade_orders, get_client
from agents.portfolio.alpaca_trading_client import place_approved_order


def run() -> dict:
    db = get_client()
    approved = get_approved_trade_orders(db)

    placed = []
    errors = []
    for trade in approved:
        order_id = trade["order_id"]
        try:
            order = place_approved_order(db, order_id)
            placed.append({"order_id": order_id, "alpaca_order_id": order.get("id")})
        except Exception as exc:
            errors.append({"order_id": order_id, "error": str(exc)})

    return {
        "orders_checked": len(approved),
        "orders_placed": len(placed),
        "error_count": len(errors),
        "placed": placed,
        "errors": errors,
    }
