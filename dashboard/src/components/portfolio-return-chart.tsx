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

import type { PortfolioSnapshot } from "@/lib/data/portfolio"

const SERIES_PORTFOLIO = "#2a78d6" // categorical slot 1 (blue) -- validated, see dataviz skill
const INK_MUTED = "#898781" // reference/benchmark line -- neutral, not a data series hue

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
    <div className="rounded-lg border bg-popover px-3 py-2 text-sm shadow-md">
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
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
        <Line
          type="monotone"
          dataKey="Portfolio"
          stroke={SERIES_PORTFOLIO}
          strokeWidth={2}
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
      </LineChart>
    </ResponsiveContainer>
  )
}
