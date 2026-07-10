import { createAnonClient } from "@/lib/supabase/client"
import { fetchAllRows, normalizeToOne } from "@/lib/supabase/paginate"

// Recent-window bound for all three signal tables, so this page never scans
// full table history -- matches health_score.py's per-component lookback
// convention (agents/workers/financial_overlay/health_score.py).
const WINDOW_DAYS = 90
const CCU_HISTORY_LIMIT = 15
const GAME_FETCH_CHUNK_SIZE = 150

export type PlayerMetricPoint = {
  date: string
  concurrent_players: number | null
}

export type LatestPlayerMetric = {
  date: string
  concurrent_players: number | null
  peak_players_24h: number | null
  review_score: number | null
  review_count: number | null
  review_velocity: number | null
}

export type CadenceStatus = "baseline" | "on_pace" | "slowing" | "absent"

export type SignalCard = {
  game_id: string
  title: string
  steam_app_id: string | null
  rawg_slug: string | null
  studio: { name: string; ticker: string | null } | null
  ccu_history: PlayerMetricPoint[]
  latest_player_metric: LatestPlayerMetric | null
  // Community-only average (source != 'news') -- see agents/synthesis/agent.py
  // _sentiment_by_game()'s docstring for why news stays a separate axis.
  sentiment_score: number | null
  sentiment_themes: string[]
  sentiment_divergence_flag: boolean
  patch_cadence_status: CadenceStatus | null
  patch_latest_date: string | null
  patch_monetization_without_content: boolean
}

type PlayerMetricRow = {
  game_id: string | null
  date: string
  concurrent_players: number | null
  peak_players_24h: number | null
  review_score: number | null
  review_count: number | null
  review_velocity: number | null
}

type SentimentRow = {
  game_id: string | null
  date: string
  source: string | null
  sentiment_score: number | null
  top_themes: { aspect?: string; polarity?: string }[] | null
  divergence_flag: boolean | null
}

type PatchRow = {
  game_id: string | null
  date: string
  cadence_status: CadenceStatus | null
  monetization_without_content: boolean | null
}

type GameRow = {
  game_id: string
  title: string
  steam_app_id: string | null
  rawg_slug: string | null
  studio_id: string | null
  studios: { name: string; ticker: string | null } | { name: string; ticker: string | null }[] | null
}

function windowCutoffDate(): string {
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - WINDOW_DAYS)
  return cutoff.toISOString().slice(0, 10)
}

function chunk<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = []
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size))
  }
  return chunks
}

function groupByGameId<T extends { game_id: string | null }>(rows: T[]): Map<string, T[]> {
  const grouped = new Map<string, T[]>()
  for (const row of rows) {
    if (!row.game_id) continue
    const list = grouped.get(row.game_id)
    if (list) {
      list.push(row)
    } else {
      grouped.set(row.game_id, [row])
    }
  }
  return grouped
}

// Scopes every signal table to active games -- same scoping every worker uses.
async function getActiveWatchlistGameIds(): Promise<Set<string>> {
  const supabase = createAnonClient()
  const rows = await fetchAllRows<{ game_id: string | null }>((from, to) =>
    supabase.from("watchlist").select("game_id").eq("active", true).range(from, to)
  )
  const ids = new Set<string>()
  for (const row of rows) {
    if (row.game_id) ids.add(row.game_id)
  }
  return ids
}

/**
 * Fetches per-game signal cards for the /signals page.
 *
 * Design: the watchlist has ~4,017 active games but ~86-87% have never
 * received a player_metrics/sentiment_snapshots row (see tasks.md's Supabase
 * Data-Gap Audit). Rendering a card per active game would mostly be blank, so
 * this bounds each of the three signal tables to a recent date window, takes
 * the union of game_ids with at least one row in any of them (intersected
 * with the active watchlist), and only fetches games/studios for that
 * "data-eligible" subset -- never the full watchlist.
 */
export async function getSignalCards(): Promise<SignalCard[]> {
  const supabase = createAnonClient()
  const cutoff = windowCutoffDate()

  const [activeIds, playerRows, sentimentRows, patchRows] = await Promise.all([
    getActiveWatchlistGameIds(),
    fetchAllRows<PlayerMetricRow>((from, to) =>
      supabase
        .from("player_metrics")
        .select("game_id, date, concurrent_players, peak_players_24h, review_score, review_count, review_velocity")
        .gte("date", cutoff)
        .order("date", { ascending: false })
        .range(from, to)
    ),
    fetchAllRows<SentimentRow>((from, to) =>
      supabase
        .from("sentiment_snapshots")
        .select("game_id, date, source, sentiment_score, top_themes, divergence_flag")
        .gte("date", cutoff)
        .order("date", { ascending: false })
        .range(from, to)
    ),
    fetchAllRows<PatchRow>((from, to) =>
      supabase
        .from("patch_events")
        .select("game_id, date, cadence_status, monetization_without_content")
        .gte("date", cutoff)
        .order("date", { ascending: false })
        .range(from, to)
    ),
  ])

  const eligibleIds = new Set<string>()
  for (const row of playerRows) if (row.game_id && activeIds.has(row.game_id)) eligibleIds.add(row.game_id)
  for (const row of sentimentRows) if (row.game_id && activeIds.has(row.game_id)) eligibleIds.add(row.game_id)
  for (const row of patchRows) if (row.game_id && activeIds.has(row.game_id)) eligibleIds.add(row.game_id)

  if (eligibleIds.size === 0) return []

  const idList = Array.from(eligibleIds)
  const gameChunkResults = await Promise.all(
    chunk(idList, GAME_FETCH_CHUNK_SIZE).map((ids) =>
      supabase
        .from("games")
        .select("game_id, title, steam_app_id, rawg_slug, studio_id, studios(name, ticker)")
        .in("game_id", ids)
    )
  )
  for (const res of gameChunkResults) {
    if (res.error) throw new Error(`Failed to load games: ${res.error.message}`)
  }
  const gameRows = gameChunkResults.flatMap((res) => (res.data ?? [])) as GameRow[]

  const playerByGame = groupByGameId(playerRows)
  const sentimentByGame = groupByGameId(sentimentRows)
  const patchByGame = groupByGameId(patchRows)

  const cards: SignalCard[] = gameRows.map((game) => {
    const studio = normalizeToOne(game.studios)

    const playerHistory = (playerByGame.get(game.game_id) ?? [])
      .slice()
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
    const ccuHistory: PlayerMetricPoint[] = playerHistory.slice(-CCU_HISTORY_LIMIT).map((r) => ({
      date: r.date,
      concurrent_players: r.concurrent_players,
    }))
    const latestPlayerMetric: LatestPlayerMetric | null =
      playerHistory.length > 0 ? playerHistory[playerHistory.length - 1] : null

    const sentimentRowsForGame = sentimentByGame.get(game.game_id) ?? []
    const communityRows = sentimentRowsForGame.filter((r) => r.source !== "news")
    const communityScores = communityRows
      .map((r) => r.sentiment_score)
      .filter((s): s is number => s !== null && s !== undefined)
    const sentimentScore =
      communityScores.length > 0
        ? communityScores.reduce((a, b) => a + b, 0) / communityScores.length
        : null
    const latestCommunityRow = communityRows
      .slice()
      .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))[0]
    const sentimentThemes = (latestCommunityRow?.top_themes ?? [])
      .map((t) => t?.aspect)
      .filter((a): a is string => Boolean(a))
      .slice(0, 3)
    const sentimentDivergenceFlag = communityRows.some((r) => r.divergence_flag)

    const latestPatch = patchRowsForGame(patchByGame.get(game.game_id) ?? [])

    return {
      game_id: game.game_id,
      title: game.title,
      steam_app_id: game.steam_app_id,
      rawg_slug: game.rawg_slug,
      studio,
      ccu_history: ccuHistory,
      latest_player_metric: latestPlayerMetric,
      sentiment_score: sentimentScore,
      sentiment_themes: sentimentThemes,
      sentiment_divergence_flag: sentimentDivergenceFlag,
      patch_cadence_status: latestPatch?.cadence_status ?? null,
      patch_latest_date: latestPatch?.date ?? null,
      patch_monetization_without_content: latestPatch?.monetization_without_content ?? false,
    }
  })

  return cards.sort((a, b) => a.title.localeCompare(b.title))
}

function patchRowsForGame(rows: PatchRow[]): PatchRow | null {
  if (rows.length === 0) return null
  return rows.slice().sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))[0]
}
