import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeftIcon } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { SentimentTrendChart, SOURCE_LABEL } from "@/components/sentiment-trend-chart"
import { CcuIntradayChart } from "@/components/ccu-intraday-chart"
import { getSentimentTrend } from "@/lib/data/sentiment-trend"
import { getCcuIntraday } from "@/lib/data/ccu-intraday"

// Sentiment history changes with every weekly worker run -- must never serve
// a build-time snapshot (same bug this dashboard already hit once, see
// tasks.md's Phase 7 notes).
export const dynamic = "force-dynamic"

export default async function SentimentTrendPage({
  params,
}: {
  params: Promise<{ gameId: string }>
}) {
  const { gameId } = await params
  const trend = await getSentimentTrend(gameId)

  if (!trend) notFound()

  const ccuHistory = await getCcuIntraday(gameId)
  const notes = trend.snapshots.filter((s) => s.vocal_minority_note)

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <Link
          href="/signals"
          className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3.5" />
          Back to Game Signals
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{trend.game.title}</h1>
        {trend.game.studio && (
          <p className="text-sm text-muted-foreground">
            {trend.game.studio.name}
            {trend.game.studio.ticker ? ` (${trend.game.studio.ticker})` : ""}
          </p>
        )}
      </div>

      {ccuHistory.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Intraday CCU</CardTitle>
            <CardDescription>
              Concurrent players over the last 48 hours, sampled hourly -- Steam's API only exposes a
              current-value snapshot, so this history is built from our own hourly polling rather than
              an official historical endpoint.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CcuIntradayChart history={ccuHistory} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Sentiment Trend</CardTitle>
          <CardDescription>
            Community sources (Reddit / Steam / YouTube) vs. news coverage. News tracks narrative
            framing, not community sentiment, and is never averaged into the community series --
            see the dashed line above.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SentimentTrendChart snapshots={trend.snapshots} />
        </CardContent>
      </Card>

      {notes.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Flags &amp; Notes</CardTitle>
            <CardDescription>
              Vocal-minority guard notes and preliminary lagged-sentiment flags from the sentiment
              worker -- authoritative divergence is decided in synthesis, not here.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {notes.map((s, i) => (
              <div key={`${s.source}-${s.date}-${i}`} className="text-sm">
                <div className="text-muted-foreground">
                  {new Date(s.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })} ·{" "}
                  {SOURCE_LABEL[s.source]}
                </div>
                <div>{s.vocal_minority_note}</div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
