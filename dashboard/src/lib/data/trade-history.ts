import { createAnonClient } from "@/lib/supabase/client"
import { fetchAllRows, normalizeToOne } from "@/lib/supabase/paginate"

export type TradeHistoryOrder = {
  order_id: string
  plan_id: string
  ticker: string
  action: "buy" | "sell" | "hold"
  size_usd: number | null
  alpaca_order_id: string | null
  status: string
  filled_at: string | null
  created_at: string
  trade_plans: {
    week_of: string
    claude_rationale: string | null
    status: string
  } | null
}

export async function getTradeHistory(): Promise<TradeHistoryOrder[]> {
  const supabase = createAnonClient()
  const rows = await fetchAllRows<Record<string, unknown>>((from, to) =>
    supabase
      .from("trade_orders")
      .select(
        "order_id, plan_id, ticker, action, size_usd, alpaca_order_id, status, filled_at, created_at, trade_plans(week_of, claude_rationale, status)"
      )
      .order("created_at", { ascending: false })
      .range(from, to)
  )

  // trade_plans is a to-one embed (trade_orders.plan_id -> trade_plans.plan_id).
  return rows.map((row) => ({
    ...row,
    trade_plans: normalizeToOne(row.trade_plans as TradeHistoryOrder["trade_plans"] | TradeHistoryOrder["trade_plans"][]),
  })) as TradeHistoryOrder[]
}
