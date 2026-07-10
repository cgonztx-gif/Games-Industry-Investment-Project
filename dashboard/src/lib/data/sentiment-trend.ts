import { createAnonClient } from "@/lib/supabase/client"
import { fetchAllRows, normalizeToOne } from "@/lib/supabase/paginate"

// The four sentiment_snapshots.source values. "news" is a different axis
// (narrative framing, not community sentiment) and must never be averaged or
// blended with the other three -- see CLAUDE.md's "Sentiment pipeline
// internals" note and agents/synthesis/agent.py::_sentiment_by_game()'s
// docstring. This page renders it as its own visually distinct series rather
// than folding it into a community trend line.
export type SentimentSource = "reddit" | "steam" | "youtube" | "news"
const KNOWN_SOURCES: SentimentSource[] = ["reddit", "steam", "youtube", "news"]

function isKnownSource(source: string | null): source is SentimentSource {
  return KNOWN_SOURCES.includes(source as SentimentSource)
}

export type SentimentSnapshotPoint = {
  date: string
  source: SentimentSource
  sentiment_score: number | null
  // Community rows use {aspect, polarity}; news rows use {frame, stance} --
  // agents/workers/sentiment/news_stance_client.py writes a different shape
  // than absa_client.py, so this stays a loose bag of string fields rather
  // than a single named shape.
  top_themes: Record<string, string | undefined>[] | null
  divergence_flag: boolean
  vocal_minority_note: string | null
}

export type SentimentTrend = {
  game: {
    game_id: string
    title: string
    studio: { name: string; ticker: string | null } | null
  }
  snapshots: SentimentSnapshotPoint[]
}

type SentimentRow = {
  date: string
  source: string | null
  sentiment_score: number | null
  top_themes: Record<string, string | undefined>[] | null
  divergence_flag: boolean | null
  vocal_minority_note: string | null
}

type GameRow = {
  game_id: string
  title: string
  studios: { name: string; ticker: string | null } | { name: string; ticker: string | null }[] | null
}

/**
 * Fetches one game's full sentiment_snapshots history for the /signals/[gameId]
 * detail page -- deliberately no date window (unlike getSignalCards()'s
 * 90-day bound): live production data only spans ~2026-07-01 to 2026-07-08
 * today (verified live before writing this), so a fixed window would be
 * pointless now and would silently start truncating history later. Returns
 * null if the game_id doesn't exist so the page can 404.
 */
export async function getSentimentTrend(gameId: string): Promise<SentimentTrend | null> {
  const supabase = createAnonClient()

  const { data: gameRow, error: gameError } = await supabase
    .from("games")
    .select("game_id, title, studios(name, ticker)")
    .eq("game_id", gameId)
    .maybeSingle()

  if (gameError) throw new Error(`Failed to load game: ${gameError.message}`)
  if (!gameRow) return null

  const typedGameRow = gameRow as GameRow
  const studio = normalizeToOne(typedGameRow.studios)

  const rows = await fetchAllRows<SentimentRow>((from, to) =>
    supabase
      .from("sentiment_snapshots")
      .select("date, source, sentiment_score, top_themes, divergence_flag, vocal_minority_note")
      .eq("game_id", gameId)
      .order("date", { ascending: true })
      .range(from, to)
  )

  const snapshots: SentimentSnapshotPoint[] = rows
    .filter((r) => isKnownSource(r.source))
    .map((r) => ({
      date: r.date,
      source: r.source as SentimentSource,
      sentiment_score: r.sentiment_score,
      top_themes: r.top_themes,
      divergence_flag: r.divergence_flag ?? false,
      vocal_minority_note: r.vocal_minority_note,
    }))

  return {
    game: { game_id: typedGameRow.game_id, title: typedGameRow.title, studio },
    snapshots,
  }
}
