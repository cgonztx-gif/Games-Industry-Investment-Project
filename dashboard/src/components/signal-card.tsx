import Link from "next/link"
import { ExternalLinkIcon, LineChartIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { CcuSparkline } from "@/components/ccu-sparkline"
import type { CadenceStatus, SignalCard as SignalCardData } from "@/lib/data/signals"

// Status palette per the dataviz skill (references/palette.md) -- reserved
// for state, distinct from the chart's categorical series hues.
const STATUS_GOOD = "#0ca30c"
const STATUS_WARNING = "#fab219"
const STATUS_CRITICAL = "#d03b3b"
const STATUS_NEUTRAL = "#898781" // muted ink -- "baseline"/no-signal-yet

const CADENCE_LABEL: Record<CadenceStatus, string> = {
  on_pace: "On pace",
  baseline: "Baseline",
  slowing: "Slowing",
  absent: "Absent",
}

const CADENCE_COLOR: Record<CadenceStatus, string> = {
  on_pace: STATUS_GOOD,
  baseline: STATUS_NEUTRAL,
  slowing: STATUS_WARNING,
  absent: STATUS_CRITICAL,
}

function sentimentColor(score: number): string {
  // Same >=6.5 / <=3.5 thresholds agents/synthesis/agent.py uses for its
  // bullish/bearish divergence check -- keeps the meaning of "good"/"bad"
  // consistent between this dashboard and the synthesis briefing.
  if (score >= 6.5) return STATUS_GOOD
  if (score <= 3.5) return STATUS_CRITICAL
  return STATUS_NEUTRAL
}

function StatusDot({ color }: { color: string }) {
  return <span className="inline-block size-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
}

function NoData({ label }: { label: string }) {
  return <p className="text-sm text-muted-foreground">{label}</p>
}

function quickLink(card: SignalCardData): { label: string; href: string } | null {
  if (card.steam_app_id) {
    return { label: "View on Steam", href: `https://store.steampowered.com/app/${card.steam_app_id}` }
  }
  if (card.rawg_slug) {
    return { label: "View on RAWG", href: `https://rawg.io/games/${card.rawg_slug}` }
  }
  return null
}

export function SignalCard({ card }: { card: SignalCardData }) {
  const link = quickLink(card)

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{card.title}</CardTitle>
            {card.studio && (
              <CardDescription>
                {card.studio.name}
                {card.studio.ticker ? ` (${card.studio.ticker})` : ""}
              </CardDescription>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">CCU trend</div>
          {card.ccu_history.length === 0 ? (
            <NoData label="No player-count data" />
          ) : (
            <div className="space-y-1">
              <CcuSparkline history={card.ccu_history} />
              <div className="text-sm tabular-nums">
                {card.latest_player_metric?.concurrent_players?.toLocaleString() ?? "—"} current
                {card.latest_player_metric?.peak_players_24h != null && (
                  <span className="text-muted-foreground">
                    {" "}
                    · {card.latest_player_metric.peak_players_24h.toLocaleString()} peak (24h)
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">Community sentiment</div>
          {card.sentiment_score === null ? (
            <NoData label="No sentiment data" />
          ) : (
            <div className="space-y-1.5">
              <Badge variant="outline" className="gap-1.5">
                <StatusDot color={sentimentColor(card.sentiment_score)} />
                <span className="tabular-nums">{card.sentiment_score.toFixed(1)} / 10</span>
              </Badge>
              {card.sentiment_themes.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {card.sentiment_themes.map((theme) => (
                    <Badge key={theme} variant="secondary">
                      {theme}
                    </Badge>
                  ))}
                </div>
              )}
              {card.sentiment_divergence_flag && (
                <p className="text-xs text-muted-foreground">
                  Preliminary lagged-sentiment flag -- authoritative divergence is decided in synthesis.
                </p>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">Patch cadence</div>
          {card.patch_cadence_status === null ? (
            <NoData label="No patch data" />
          ) : (
            <div className="space-y-1">
              <Badge variant="outline" className="gap-1.5">
                <StatusDot color={CADENCE_COLOR[card.patch_cadence_status]} />
                {CADENCE_LABEL[card.patch_cadence_status]}
              </Badge>
              {card.patch_monetization_without_content && (
                <p className="text-xs text-muted-foreground">Monetization update without content</p>
              )}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Link
            href={`/signals/${card.game_id}`}
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            View sentiment trend
            <LineChartIcon className="size-3.5" />
          </Link>
          {link && (
            <a
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
            >
              {link.label}
              <ExternalLinkIcon className="size-3.5" />
            </a>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
