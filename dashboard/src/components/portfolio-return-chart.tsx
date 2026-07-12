"use client"

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import type { PortfolioSnapshot } from "@/lib/data/portfolio"
import { BRAND } from "@/lib/status-colors"

const SERIES_PORTFOLIO = BRAND // brand accent -- the one hero single-series line
const INK_MUTED = "#71717a" // reference/benchmark line -- neutral, not a data series hue

function formatPct(value: number | null) {
  if (value === null || value === undefined) return "—"
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: { value: number | null; name: string; color: string }[]
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-sm shadow-md">
      <div className="mb-1 font-medium text-popover-foreground">{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2 text-muted-foreground">
          <span
            className="size-2 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span>{entry.name}:</span>
          <span className="font-medium tabular-nums text-popover-foreground">
            {formatPct(entry.value)}
          </span>
        </div>
      ))}
    </div>
  )
}

export function PortfolioReturnChart({ snapshots }: { snapshots: PortfolioSnapshot[] }) {
  const data = snapshots.map((s) => ({
    date: new Date(s.date).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    Portfolio: s.total_return_pct,
    "S&P 500": s.benchmark_return_pct,
  }))

  return (
    <ResponsiveContainer
      width="100%"
      height={280}
      className="[&_.recharts-area-curve]:drop-shadow-[0_0_6px_rgba(34,197,94,0.5)]"
    >
      <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="portfolioReturnGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={SERIES_PORTFOLIO} stopOpacity={0.35} />
            <stop offset="100%" stopColor={SERIES_PORTFOLIO} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
        />
        <YAxis
          tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}%`}
          width={48}
        />
        <Tooltip content={<ChartTooltip />} />
        <Area
          type="monotone"
          dataKey="Portfolio"
          stroke={SERIES_PORTFOLIO}
          strokeWidth={2}
          fill="url(#portfolioReturnGradient)"
          dot={false}
          activeDot={{ r: 4 }}
        />
        <Line
          type="monotone"
          dataKey="S&P 500"
          stroke={INK_MUTED}
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={false}
          activeDot={{ r: 4 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
