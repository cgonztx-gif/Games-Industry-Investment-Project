"use client"

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import type { SentimentSnapshotPoint, SentimentSource } from "@/lib/data/sentiment-trend"

// Fixed categorical order/hues (dataviz skill, references/palette.md) --
// reused identically across every game's chart, never cycled or reassigned
// per game. "news" deliberately sits outside the community categorical slots
// (its own hue + a dashed stroke) since it's a different axis that must
// never be blended into a community sentiment line -- see
// sentiment-trend.ts's SentimentSource docstring and CLAUDE.md's "Sentiment
// pipeline internals" note.
const COMMUNITY_SOURCES: SentimentSource[] = ["reddit", "steam", "youtube"]
const ALL_SOURCES: SentimentSource[] = [...COMMUNITY_SOURCES, "news"]

export const SOURCE_LABEL: Record<SentimentSource, string> = {
  reddit: "Reddit",
  steam: "Steam",
  youtube: "YouTube",
  news: "News",
}

const SOURCE_COLOR: Record<SentimentSource, string> = {
  steam: "#2a78d6", // categorical slot 1 (blue)
  reddit: "#1baf7a", // categorical slot 2 (aqua)
  youtube: "#eda100", // categorical slot 3 (yellow)
  news: "#e34948", // categorical slot 6 (red) -- distinct from the three
  // community slots above; paired with a dashed stroke below so it never
  // reads as a fourth community source.
}

type ChartRow = {
  date: string
  reddit?: number | null
  reddit_divergence?: boolean
  reddit_themes?: string
  steam?: number | null
  steam_divergence?: boolean
  steam_themes?: string
  youtube?: number | null
  youtube_divergence?: boolean
  youtube_themes?: string
  news?: number | null
  news_divergence?: boolean
  news_themes?: string
}

function valueFor(row: ChartRow, source: SentimentSource): number | null | undefined {
  switch (source) {
    case "reddit":
      return row.reddit
    case "steam":
      return row.steam
    case "youtube":
      return row.youtube
    case "news":
      return row.news
  }
}

function divergenceFor(row: ChartRow, source: SentimentSource): boolean {
  switch (source) {
    case "reddit":
      return Boolean(row.reddit_divergence)
    case "steam":
      return Boolean(row.steam_divergence)
    case "youtube":
      return Boolean(row.youtube_divergence)
    case "news":
      return Boolean(row.news_divergence)
  }
}

function themesFor(row: ChartRow, source: SentimentSource): string | undefined {
  switch (source) {
    case "reddit":
      return row.reddit_themes
    case "steam":
      return row.steam_themes
    case "youtube":
      return row.youtube_themes
    case "news":
      return row.news_themes
  }
}

// Community rows carry {aspect, polarity}; news rows carry {frame, stance}
// (agents/workers/sentiment/news_stance_client.py writes a different shape
// than absa_client.py) -- read whichever pair is present.
function themeSummary(point: SentimentSnapshotPoint): string | undefined {
  if (!point.top_themes || point.top_themes.length === 0) return undefined
  const parts = point.top_themes
    .map((t) => {
      const label = t.aspect ?? t.frame
      const value = t.polarity ?? t.stance
      return label ? `${label}${value ? ` (${value})` : ""}` : null
    })
    .filter((s): s is string => Boolean(s))
  return parts.length > 0 ? parts.join(", ") : undefined
}

function applyPoint(row: ChartRow, point: SentimentSnapshotPoint): void {
  const themes = themeSummary(point)
  switch (point.source) {
    case "reddit":
      row.reddit = point.sentiment_score
      row.reddit_divergence = point.divergence_flag
      if (themes) row.reddit_themes = themes
      break
    case "steam":
      row.steam = point.sentiment_score
      row.steam_divergence = point.divergence_flag
      if (themes) row.steam_themes = themes
      break
    case "youtube":
      row.youtube = point.sentiment_score
      row.youtube_divergence = point.divergence_flag
      if (themes) row.youtube_themes = themes
      break
    case "news":
      row.news = point.sentiment_score
      row.news_divergence = point.divergence_flag
      if (themes) row.news_themes = themes
      break
  }
}

function buildChartData(snapshots: SentimentSnapshotPoint[]): ChartRow[] {
  const byDate = new Map<string, ChartRow>()
  for (const point of snapshots) {
    const row = byDate.get(point.date) ?? { date: point.date }
    applyPoint(row, point)
    byDate.set(point.date, row)
  }
  return Array.from(byDate.values()).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
}

function formatDate(date: string) {
  return new Date(date).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function ChartTooltip({
  active,
  payload,
  sourcesPresent,
}: {
  active?: boolean
  payload?: { payload: ChartRow }[]
  label?: string
  sourcesPresent: SentimentSource[]
}) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  const entries = sourcesPresent.filter((s) => valueFor(row, s) !== null && valueFor(row, s) !== undefined)
  if (entries.length === 0) return null

  return (
    <div className="max-w-xs rounded-lg border bg-popover px-3 py-2 text-sm shadow-md">
      <div className="mb-1 font-medium text-popover-foreground">{formatDate(row.date)}</div>
      <div className="space-y-2">
        {entries.map((source) => (
          <div key={source}>
            <div className="flex items-center gap-2 text-muted-foreground">
              <span className="size-2 rounded-full" style={{ backgroundColor: SOURCE_COLOR[source] }} />
              <span>{SOURCE_LABEL[source]}:</span>
              <span className="font-medium tabular-nums text-popover-foreground">
                {valueFor(row, source)?.toFixed(1)} / 10
              </span>
            </div>
            {themesFor(row, source) && (
              <div className="pl-4 text-xs text-muted-foreground">{themesFor(row, source)}</div>
            )}
            {divergenceFor(row, source) && (
              <div className="pl-4 text-xs text-muted-foreground">Preliminary lagged-sentiment flag</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function renderDivergenceDot(color: string, source: SentimentSource) {
  return function DivergenceDot(props: { cx?: number; cy?: number; payload?: ChartRow }) {
    const { cx, cy, payload } = props
    if (cx === undefined || cy === undefined || !payload) return null
    const value = valueFor(payload, source)
    if (value === null || value === undefined) return null
    if (divergenceFor(payload, source)) {
      return <circle cx={cx} cy={cy} r={6} fill={color} stroke="var(--popover)" strokeWidth={2} />
    }
    return <circle cx={cx} cy={cy} r={3} fill={color} />
  }
}

export function SentimentTrendChart({ snapshots }: { snapshots: SentimentSnapshotPoint[] }) {
  const data = buildChartData(snapshots)
  const sourcesPresent = ALL_SOURCES.filter((source) => snapshots.some((s) => s.source === source))

  if (sourcesPresent.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        No sentiment data for this game yet.
      </p>
    )
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
        {sourcesPresent.map((source) => (
          <span key={source} className="flex items-center gap-1.5">
            {source === "news" ? (
              <span className="inline-block w-4" style={{ borderTop: `2px dashed ${SOURCE_COLOR[source]}` }} />
            ) : (
              <span className="inline-block h-0.5 w-4 rounded-full" style={{ backgroundColor: SOURCE_COLOR[source] }} />
            )}
            {SOURCE_LABEL[source]}
            {source === "news" && " (narrative, not community sentiment)"}
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
          />
          <YAxis
            domain={[1, 10]}
            tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
            width={32}
          />
          <Tooltip content={<ChartTooltip sourcesPresent={sourcesPresent} />} />
          {sourcesPresent.map((source) => (
            <Line
              key={source}
              type="monotone"
              dataKey={source}
              name={SOURCE_LABEL[source]}
              stroke={SOURCE_COLOR[source]}
              strokeWidth={2}
              strokeDasharray={source === "news" ? "4 4" : undefined}
              dot={renderDivergenceDot(SOURCE_COLOR[source], source)}
              activeDot={{ r: 5 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
