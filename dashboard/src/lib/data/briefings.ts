import { createAnonClient } from "@/lib/supabase/client"

// Shapes written by agents/synthesis/agent.py::run() (verified against two
// real production rows before writing this, both with empty
// top_opportunities/risk_flags arrays -- flat portfolio_update/notable_events
// confirmed, but no populated opportunity/risk item exists live yet).

export type PortfolioUpdate = {
  confidence: "very_low" | "low" | "medium"
  equity_signals_count: number
  divergence_count: number
  risk_count: number
}

export type NotableEvents = {
  patch_events: number
  studio_signals: number
}

export type TopOpportunity = {
  game_id: string
  type: "bearish_text_stable_quant"
  sentiment_score: number
  concurrent_players: number | null
  review_velocity: number | null
  patch_activity_this_week: boolean
  interpretation: string
  deep_dive_triggered?: boolean
  deep_dive?: string | null
}

// risk_flags mixes two different origins in one array -- game-level rows
// carry game_id/sentiment_score/concurrent_players/review_velocity,
// studio-level rows carry studio_id/description instead. `signal` is present
// on both but typed loosely (string | null) since studio_signals.signal_type
// isn't a fixed enum in the schema.
export type RiskFlag = {
  severity: string
  signal: string | null
  game_id?: string
  sentiment_score?: number
  concurrent_players?: number | null
  review_velocity?: number | null
  studio_id?: string
  description?: string | null
}

export type WeeklyBriefing = {
  id: string
  week_of: string
  briefing_text: string | null
  portfolio_update: PortfolioUpdate | null
  top_opportunities: TopOpportunity[] | null
  risk_flags: RiskFlag[] | null
  notable_events: NotableEvents | null
  reasoning_log: string | null
  created_at: string
}

export async function getBriefings(): Promise<WeeklyBriefing[]> {
  const supabase = createAnonClient()
  const { data, error } = await supabase
    .from("weekly_briefings")
    .select(
      "id, week_of, briefing_text, portfolio_update, top_opportunities, risk_flags, notable_events, reasoning_log, created_at"
    )
    .order("week_of", { ascending: false })

  if (error) {
    throw new Error(`Failed to load weekly briefings: ${error.message}`)
  }
  return (data ?? []) as WeeklyBriefing[]
}

export type EntityLookups = {
  gameTitles: Map<string, string>
  studioNames: Map<string, { name: string; ticker: string | null }>
}

/**
 * Collects every game_id/studio_id referenced across all briefings'
 * top_opportunities/risk_flags JSONB arrays and resolves them in exactly two
 * batch queries -- not one query per briefing per item. Both sets stay small
 * at this weekly cadence, so a single whole-page-load batch is correct.
 */
export async function getEntityLookups(briefings: WeeklyBriefing[]): Promise<EntityLookups> {
  const gameIds = new Set<string>()
  const studioIds = new Set<string>()

  for (const briefing of briefings) {
    for (const item of briefing.top_opportunities ?? []) {
      if (item.game_id) gameIds.add(item.game_id)
    }
    for (const risk of briefing.risk_flags ?? []) {
      if (risk.game_id) gameIds.add(risk.game_id)
      if (risk.studio_id) studioIds.add(risk.studio_id)
    }
  }

  const gameTitles = new Map<string, string>()
  const studioNames = new Map<string, { name: string; ticker: string | null }>()

  if (gameIds.size === 0 && studioIds.size === 0) {
    return { gameTitles, studioNames }
  }

  const supabase = createAnonClient()
  const [gamesRes, studiosRes] = await Promise.all([
    gameIds.size > 0
      ? supabase.from("games").select("game_id, title").in("game_id", Array.from(gameIds))
      : Promise.resolve({ data: [], error: null }),
    studioIds.size > 0
      ? supabase.from("studios").select("studio_id, name, ticker").in("studio_id", Array.from(studioIds))
      : Promise.resolve({ data: [], error: null }),
  ])

  if (gamesRes.error) throw new Error(`Failed to load games: ${gamesRes.error.message}`)
  if (studiosRes.error) throw new Error(`Failed to load studios: ${studiosRes.error.message}`)

  for (const row of (gamesRes.data ?? []) as { game_id: string; title: string }[]) {
    gameTitles.set(row.game_id, row.title)
  }
  for (const row of (studiosRes.data ?? []) as { studio_id: string; name: string; ticker: string | null }[]) {
    studioNames.set(row.studio_id, { name: row.name, ticker: row.ticker })
  }

  return { gameTitles, studioNames }
}
