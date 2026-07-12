import { createAnonClient } from "@/lib/supabase/client"

export type Position = {
  position_id: string
  ticker: string
  qty: number | null
  avg_entry_price: number | null
  current_price: number | null
  unrealized_pnl: number | null
  as_of: string
}

export type PortfolioSnapshot = {
  snapshot_id: string
  date: string
  total_value: number | null
  cash: number | null
  total_return_pct: number | null
  benchmark_return_pct: number | null
}

export async function getCurrentPositions(): Promise<Position[]> {
  const supabase = createAnonClient()
  const { data, error } = await supabase
    .from("positions")
    .select("position_id, ticker, qty, avg_entry_price, current_price, unrealized_pnl, as_of")
    .order("ticker", { ascending: true })

  if (error) {
    throw new Error(`Failed to load positions: ${error.message}`)
  }
  return data ?? []
}

export async function getPortfolioSnapshots(): Promise<PortfolioSnapshot[]> {
  const supabase = createAnonClient()
  const { data, error } = await supabase
    .from("portfolio_snapshots")
    .select("snapshot_id, date, total_value, cash, total_return_pct, benchmark_return_pct")
    .order("date", { ascending: true })

  if (error) {
    throw new Error(`Failed to load portfolio snapshots: ${error.message}`)
  }
  return data ?? []
}

// Single-row lookup for the global header chip -- cheaper than fetching the
// full snapshot history just to read the last row.
export async function getLatestPortfolioSnapshot(): Promise<PortfolioSnapshot | null> {
  const supabase = createAnonClient()
  const { data, error } = await supabase
    .from("portfolio_snapshots")
    .select("snapshot_id, date, total_value, cash, total_return_pct, benchmark_return_pct")
    .order("date", { ascending: false })
    .limit(1)
    .maybeSingle()

  if (error) {
    throw new Error(`Failed to load latest portfolio snapshot: ${error.message}`)
  }
  return data
}
