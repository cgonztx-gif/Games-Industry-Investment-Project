import { createAnonClient } from "@/lib/supabase/client"

export type PendingProposal = {
  proposal_id: string
  game_id: string | null
  studio_id: string | null
  trigger_signal: string | null
  claude_rationale: string | null
  status: string
  score: number | null
  recommended_sentiment_tier: string | null
  created_at: string
  games: {
    title: string
    steam_app_id: string | null
    rawg_slug: string | null
  } | null
  studios: {
    name: string
    ticker: string | null
  } | null
}

export async function getPendingProposals(): Promise<PendingProposal[]> {
  const supabase = createAnonClient()
  const { data, error } = await supabase
    .from("watchlist_proposals")
    .select(
      "proposal_id, game_id, studio_id, trigger_signal, claude_rationale, status, score, recommended_sentiment_tier, created_at, games(title, steam_app_id, rawg_slug), studios(name, ticker)"
    )
    .eq("status", "pending")
    .order("created_at", { ascending: false })

  if (error) {
    throw new Error(`Failed to load watchlist proposals: ${error.message}`)
  }
  // supabase-js types embedded to-one relations as arrays; these FKs are
  // singular (game_id/studio_id each point at exactly one row), so normalize.
  return (data ?? []).map((row) => ({
    ...row,
    games: Array.isArray(row.games) ? row.games[0] ?? null : row.games,
    studios: Array.isArray(row.studios) ? row.studios[0] ?? null : row.studios,
  })) as PendingProposal[]
}
