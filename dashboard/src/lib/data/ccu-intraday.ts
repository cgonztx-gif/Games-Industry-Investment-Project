import { createAnonClient } from "@/lib/supabase/client"
import { fetchAllRows } from "@/lib/supabase/paginate"

export type CcuSnapshotPoint = {
  captured_at: string
  concurrent_players: number | null
}

type CcuSnapshotRow = {
  captured_at: string
  concurrent_players: number | null
}

/**
 * One game's recent ccu_snapshots history (see CLAUDE.md's "Intraday CCU
 * snapshots" note) for the /signals/[gameId] detail page. Bounded to a
 * rolling window, unlike getSentimentTrend()'s unbounded fetch -- hourly
 * snapshots accumulate fast enough that an unbounded query would grow
 * without purpose, since the UI only ever plots a short recent stretch.
 */
export async function getCcuIntraday(gameId: string, hours = 48): Promise<CcuSnapshotPoint[]> {
  const supabase = createAnonClient()
  const cutoff = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString()

  const rows = await fetchAllRows<CcuSnapshotRow>((from, to) =>
    supabase
      .from("ccu_snapshots")
      .select("captured_at, concurrent_players")
      .eq("game_id", gameId)
      .gte("captured_at", cutoff)
      .order("captured_at", { ascending: true })
      .range(from, to)
  )

  return rows.map((r) => ({
    captured_at: r.captured_at,
    concurrent_players: r.concurrent_players,
  }))
}
