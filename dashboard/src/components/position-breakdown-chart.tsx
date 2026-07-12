"use client"

import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import type { Position } from "@/lib/data/portfolio"

// Part-to-whole data (a handful of categories) -> horizontal stacked bar per
// the dataviz skill's form heuristic (references/choosing-a-form.md, "Part-
// to-whole" row), not a pie/donut. A single 100%-stacked row represents the
// whole portfolio (every open position + cash), colored categorically.
//
// Fixed categorical hue order (dataviz skill, references/palette.md) -- the
// same eight validated hues sentiment-trend-chart.tsx draws from. Unlike
// that chart's fixed per-source slots (steam/reddit/youtube/news never
// change identity), a position's ticker set changes week to week, so this
// is a *nominal* categorical axis (color-formula.md: swapping ticker order
// doesn't change meaning) -- slots are assigned in rank order (largest
// position first) rather than pinned per-ticker. Slot 8 (orange) is
// reserved for an "Other" fold-in once the series-count ladder's 7-series
// soft cap is exceeded, instead of generating a 9th hue.
// Dark-surface steps (validated against this theme's #0a0a0a/#131313
// surfaces -- see the plan file's contrast report; worst adjacent CVD ΔE
// 10.3, floor band, legal because this chart always ships the swatch/label
// legend below).
const CATEGORICAL_SLOTS = [
  "#3987e5", // slot 1 blue
  "#199e70", // slot 2 aqua
  "#c98500", // slot 3 yellow
  "#008300", // slot 4 green
  "#9085e9", // slot 5 violet
  "#e66767", // slot 6 red
  "#d55181", // slot 7 magenta
  "#d95926", // slot 8 orange ("Other" fold-in)
]
const MAX_NAMED_POSITIONS = 7

// Cash isn't an invested identity -- it's the uninvested "resting" state of
// capital, so (like the S&P 500 reference line in portfolio-return-chart.tsx)
// it wears neutral muted gray rather than a categorical hue.
const CASH_COLOR = "#71717a"

export type Segment = {
  key: string
  label: string
  value: number
  share: number
  color: string
  isCash: boolean
}

export function buildSegments(
  positions: Position[],
  totalValue: number,
  cash: number
): { segments: Segment[]; unavailableTickers: string[]; knownTotal: number } {
  const unavailableTickers: string[] = []
  const valued: { ticker: string; value: number }[] = []

  for (const p of positions) {
    if (p.qty === null || p.current_price === null) {
      // Missing data -- never fabricate a market value for it.
      unavailableTickers.push(p.ticker)
      continue
    }
    const value = p.qty * p.current_price
    if (value <= 0) continue // nothing meaningful to draw for a zero/negative value
    valued.push({ ticker: p.ticker, value })
  }

  valued.sort((a, b) => b.value - a.value)

  const named = valued.slice(0, MAX_NAMED_POSITIONS)
  const overflow = valued.slice(MAX_NAMED_POSITIONS)

  // Percentages and the bar's domain are both computed against knownTotal
  // (sum of what's actually drawn: valued positions + cash), not the raw
  // totalValue snapshot -- if a position's value can't be computed, using
  // totalValue as the denominator would understate every other segment's
  // share and leave the bar visibly underfilling its own track, with no way
  // for a reader to tell that gap apart from a rendering artifact. Segments
  // drawn always sum to exactly 100% of knownTotal; the caller surfaces the
  // gap via unavailableTickers instead.
  const knownTotal = valued.reduce((sum, p) => sum + p.value, 0) + Math.max(cash, 0)

  const segments: Segment[] = named.map((p, i) => ({
    key: p.ticker,
    label: p.ticker,
    value: p.value,
    share: knownTotal > 0 ? p.value / knownTotal : 0,
    color: CATEGORICAL_SLOTS[i],
    isCash: false,
  }))

  if (overflow.length > 0) {
    const overflowValue = overflow.reduce((sum, p) => sum + p.value, 0)
    segments.push({
      key: "__other__",
      label: `Other (${overflow.length})`,
      value: overflowValue,
      share: knownTotal > 0 ? overflowValue / knownTotal : 0,
      color: CATEGORICAL_SLOTS[7],
      isCash: false,
    })
  }

  if (cash > 0) {
    segments.push({
      key: "__cash__",
      label: "Cash",
      value: cash,
      share: knownTotal > 0 ? cash / knownTotal : 0,
      color: CASH_COLOR,
      isCash: true,
    })
  }

  return { segments, unavailableTickers, knownTotal }
}

function formatUsd(value: number) {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  })
}

function formatPct(share: number) {
  return `${(share * 100).toFixed(1)}%`
}

function ChartTooltip({
  active,
  payload,
  segmentsByKey,
}: {
  active?: boolean
  payload?: { dataKey?: string | number; value?: number }[]
  segmentsByKey: Map<string, Segment>
}) {
  if (!active || !payload?.length) return null
  const entries = payload
    .map((entry) => segmentsByKey.get(String(entry.dataKey)))
    .filter((s): s is Segment => Boolean(s))
  if (entries.length === 0) return null

  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-sm shadow-md">
      <div className="space-y-1.5">
        {entries.map((segment) => (
          <div key={segment.key} className="flex items-center gap-2">
            <span
              className="inline-block h-0.5 w-3 shrink-0 rounded-full"
              style={{ backgroundColor: segment.color }}
            />
            <span className="text-muted-foreground">{segment.label}:</span>
            <span className="font-medium tabular-nums text-popover-foreground">
              {formatUsd(segment.value)} ({formatPct(segment.share)})
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function PositionBreakdownChart({
  positions,
  totalValue,
  cash,
}: {
  positions: Position[]
  totalValue: number
  cash: number
}) {
  const { segments, unavailableTickers, knownTotal } = buildSegments(positions, totalValue, cash)

  if (segments.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        Composition unavailable — no position or cash values could be computed for
        the latest snapshot.
      </p>
    )
  }

  const segmentsByKey = new Map(segments.map((s) => [s.key, s]))
  const row: Record<string, number | string> = { name: "Portfolio" }
  for (const s of segments) row[s.key] = s.value

  return (
    <div>
      {/* Direct labels won't fit inside a thin composition bar's segments
          (marks-and-anatomy.md: measure first, fall back to legend + tooltip
          when a label doesn't fit) -- so the swatch/label/share row below
          carries identity and value, mirroring the always-shown legend rows
          in portfolio-return-chart.tsx and sentiment-trend-chart.tsx. Shown
          even at a single segment (e.g. 100% cash) because this card's title
          is generic ("Portfolio Composition") and doesn't itself name what's
          plotted, unlike ccu-sparkline.tsx's single-series case. */}
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
        {segments.map((segment) => (
          <span key={segment.key} className="flex items-center gap-1.5">
            <span
              className="inline-block size-2.5 rounded-full"
              style={{ backgroundColor: segment.color }}
            />
            {segment.label}
            <span className="tabular-nums text-foreground">{formatPct(segment.share)}</span>
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={64}>
        <BarChart
          data={[row]}
          layout="vertical"
          margin={{ top: 0, right: 0, left: 0, bottom: 0 }}
        >
          {/* Domain is knownTotal (sum of drawn segments), not the raw
              totalValue snapshot -- keeps the bar always filling its own
              track and every share summing to 100% of what's actually
              plotted, even when a position's value couldn't be computed. */}
          <XAxis type="number" domain={[0, knownTotal]} hide />
          <YAxis type="category" dataKey="name" hide />
          <Tooltip
            content={<ChartTooltip segmentsByKey={segmentsByKey} />}
            cursor={{ fill: "var(--muted)", opacity: 0.3 }}
          />
          {/* marks-and-anatomy.md calls for a 4px rounded outer end (square
              at inner boundaries) plus a 2px surface-color gap between
              stacked segments. Recharts' per-Bar radius prop can't target
              only the outer edge of a dynamic, variable-length segment set,
              so this uses a uniform small radius plus a `stroke="var(--card)"`
              inset as a practical approximation of both rules -- documented
              simplification, not a spec violation of intent (rounded ends +
              a visible gap between touching segments). */}
          {segments.map((segment) => (
            <Bar
              key={segment.key}
              dataKey={segment.key}
              stackId="composition"
              fill={segment.color}
              stroke="var(--card)"
              strokeWidth={2}
              radius={[4, 4, 4, 4]}
              barSize={22}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      {unavailableTickers.length > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          Value unavailable for {unavailableTickers.join(", ")} (missing quantity or
          price data) — excluded from the chart, and the percentages above are of
          known holdings only, not the full portfolio total.
        </p>
      )}
    </div>
  )
}
