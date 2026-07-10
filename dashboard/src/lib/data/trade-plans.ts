import { createAnonClient } from "@/lib/supabase/client"

export type TradeOrder = {
  order_id: string
  plan_id: string
  ticker: string
  action: "buy" | "sell" | "hold"
  size_usd: number | null
  alpaca_order_id: string | null
  status: string
  filled_at: string | null
  created_at: string
}

export type PendingTradePlan = {
  plan_id: string
  week_of: string
  briefing_id: string | null
  claude_rationale: string | null
  status: string
  reviewed_at: string | null
  created_at: string
  trade_orders: TradeOrder[]
}

export async function getPendingTradePlans(): Promise<PendingTradePlan[]> {
  const supabase = createAnonClient()
  const { data, error } = await supabase
    .from("trade_plans")
    .select(
      "plan_id, week_of, briefing_id, claude_rationale, status, reviewed_at, created_at, trade_orders(*)"
    )
    .eq("status", "pending")
    .order("created_at", { ascending: false })

  if (error) {
    throw new Error(`Failed to load trade plans: ${error.message}`)
  }
  // trade_orders is a real one-to-many FK (plan_id -> trade_plans.plan_id), so
  // supabase-js correctly types/returns it as an array already -- unlike the
  // to-one game_id/studio_id embeds in watchlist-proposals.ts, no normalization needed.
  return (data ?? []) as PendingTradePlan[]
}
