import Link from "next/link"

import { Badge } from "@/components/ui/badge"
import type { RiskFlag, TopOpportunity, WeeklyBriefing } from "@/lib/data/briefings"
import { STATUS_GOOD, STATUS_WARNING, STATUS_CRITICAL, STATUS_NEUTRAL } from "@/lib/status-colors"

const CONFIDENCE_LABEL: Record<string, string> = {
  medium: "Medium confidence",
  low: "Low confidence",
  very_low: "Very low confidence",
}

const CONFIDENCE_COLOR: Record<string, string> = {
  medium: STATUS_GOOD,
  low: STATUS_WARNING,
  very_low: STATUS_CRITICAL,
}

function severityColor(severity: string): string {
  const normalized = severity.toLowerCase()
  if (normalized === "high") return STATUS_CRITICAL
  if (normalized === "medium") return STATUS_WARNING
  if (normalized === "low") return STATUS_NEUTRAL
  return STATUS_NEUTRAL
}

function StatusDot({ color }: { color: string }) {
  return <span className="inline-block size-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
}

function formatWeekOf(weekOf: string) {
  // week_of is a Postgres `date` (bare "YYYY-MM-DD", no time component),
  // which JS parses as UTC midnight -- must format with timeZone: "UTC" or
  // any viewer west of UTC sees the previous calendar day. Same fix already
  // applied in trade-plan-card.tsx/trade-history-table.tsx's copies.
  return new Date(`${weekOf}T00:00:00Z`).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  })
}

function OpportunityItem({
  item,
  gameTitles,
}: {
  item: TopOpportunity
  gameTitles: Map<string, string>
}) {
  const title = gameTitles.get(item.game_id) ?? item.game_id

  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link href={`/signals/${item.game_id}`} className="font-medium text-primary hover:underline">
          {title}
        </Link>
        <Badge variant="outline" className="tabular-nums">
          Sentiment {item.sentiment_score.toFixed(1)} / 10
        </Badge>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
        <span>
          CCU:{" "}
          {item.concurrent_players !== null ? item.concurrent_players.toLocaleString() : "—"}
        </span>
        <span>
          Review velocity: {item.review_velocity !== null ? item.review_velocity : "—"}
        </span>
        <span>Patch this week: {item.patch_activity_this_week ? "Yes" : "No"}</span>
      </div>
      <p className="text-sm">{item.interpretation}</p>
      {item.deep_dive && (
        // deep_dive is the structured findings object run_deep_dive() writes
        // ({summary, sources, confidence}); rendering it bare as a JSX child
        // used to throw "Objects are not valid as a React child" and crash
        // the page on the first briefing with a populated deep dive.
        <div className="space-y-1.5 rounded-md bg-muted/50 p-3 text-sm">
          <div className="text-xs font-medium text-muted-foreground">
            Deep dive · {item.deep_dive.confidence} confidence
          </div>
          <p>{item.deep_dive.summary}</p>
          {(item.deep_dive.sources ?? []).length > 0 && (
            <ul className="space-y-0.5 text-xs text-muted-foreground">
              {(item.deep_dive.sources ?? []).map((source) => (
                <li key={source} className="truncate">
                  <a
                    href={source}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline"
                  >
                    {source}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function RiskItem({
  risk,
  gameTitles,
  studioNames,
}: {
  risk: RiskFlag
  gameTitles: Map<string, string>
  studioNames: Map<string, { name: string; ticker: string | null }>
}) {
  const isGameLevel = Boolean(risk.game_id)
  const studio = risk.studio_id ? studioNames.get(risk.studio_id) : null
  const title = isGameLevel
    ? gameTitles.get(risk.game_id!) ?? risk.game_id
    : studio
      ? `${studio.name}${studio.ticker ? ` (${studio.ticker})` : ""}`
      : (risk.studio_id ?? "Unknown")

  const linkHref = isGameLevel ? `/signals/${risk.game_id}` : null

  return (
    <div className="space-y-1.5 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {linkHref ? (
          <Link href={linkHref} className="font-medium text-primary hover:underline">
            {title}
          </Link>
        ) : (
          <span className="font-medium">{title}</span>
        )}
        <Badge variant="outline" className="gap-1.5">
          <StatusDot color={severityColor(risk.severity)} />
          {risk.severity}
        </Badge>
      </div>
      {risk.signal && <p className="text-sm text-muted-foreground">{risk.signal}</p>}
      {isGameLevel && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
          {risk.sentiment_score !== undefined && (
            <span>Sentiment: {risk.sentiment_score.toFixed(1)} / 10</span>
          )}
          {risk.concurrent_players !== undefined && (
            <span>
              CCU: {risk.concurrent_players !== null ? risk.concurrent_players.toLocaleString() : "—"}
            </span>
          )}
          {risk.review_velocity !== undefined && (
            <span>Review velocity: {risk.review_velocity !== null ? risk.review_velocity : "—"}</span>
          )}
        </div>
      )}
      {risk.description && <p className="text-sm">{risk.description}</p>}
    </div>
  )
}

export function BriefingDetail({
  briefing,
  gameTitles,
  studioNames,
}: {
  briefing: WeeklyBriefing
  gameTitles: Map<string, string>
  studioNames: Map<string, { name: string; ticker: string | null }>
}) {
  const update = briefing.portfolio_update
  const events = briefing.notable_events
  const opportunities = briefing.top_opportunities ?? []
  const risks = briefing.risk_flags ?? []

  return (
    <div className="space-y-5">
      {update && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="gap-1.5">
            <StatusDot color={CONFIDENCE_COLOR[update.confidence] ?? STATUS_NEUTRAL} />
            {CONFIDENCE_LABEL[update.confidence] ?? update.confidence}
          </Badge>
          <Badge variant="secondary" className="tabular-nums">
            {update.equity_signals_count} equity signal{update.equity_signals_count === 1 ? "" : "s"}
          </Badge>
          <Badge variant="secondary" className="tabular-nums">
            {update.divergence_count} divergence{update.divergence_count === 1 ? "" : "s"}
          </Badge>
          <Badge variant="secondary" className="tabular-nums">
            {update.risk_count} risk flag{update.risk_count === 1 ? "" : "s"}
          </Badge>
        </div>
      )}

      <div>
        <h3 className="mb-2 text-sm font-medium">Top Opportunities</h3>
        {opportunities.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No opportunity candidates this week.
          </p>
        ) : (
          <div className="space-y-2">
            {opportunities.map((item) => (
              <OpportunityItem key={item.game_id} item={item} gameTitles={gameTitles} />
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium">Risk Flags</h3>
        {risks.length === 0 ? (
          <p className="text-sm text-muted-foreground">No risk flags this week.</p>
        ) : (
          <div className="space-y-2">
            {risks.map((risk, i) => (
              <RiskItem
                key={risk.game_id ?? risk.studio_id ?? i}
                risk={risk}
                gameTitles={gameTitles}
                studioNames={studioNames}
              />
            ))}
          </div>
        )}
      </div>

      {events && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Notable Events</h3>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary" className="tabular-nums">
              {events.patch_events} patch event{events.patch_events === 1 ? "" : "s"}
            </Badge>
            <Badge variant="secondary" className="tabular-nums">
              {events.studio_signals} studio signal{events.studio_signals === 1 ? "" : "s"}
            </Badge>
          </div>
        </div>
      )}

      {briefing.reasoning_log && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Reasoning Log</h3>
          <p className="text-sm text-muted-foreground">{briefing.reasoning_log}</p>
        </div>
      )}
    </div>
  )
}

export { formatWeekOf }
