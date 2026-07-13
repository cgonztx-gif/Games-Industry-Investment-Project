"use client"

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import type { CcuSnapshotPoint } from "@/lib/data/ccu-intraday"
import { BRAND } from "@/lib/status-colors"

const SERIES_CCU = BRAND

// Datetime-aware, unlike ccu-sparkline.tsx's day-only tooltip -- multiple
// points can share a calendar day here, so the label needs hour:minute too.
function formatTimestamp(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: { value: number | null; payload: { captured_at: string } }[]
}) {
  if (!active || !payload?.length) return null
  const point = payload[0]
  if (point.value === null || point.value === undefined) return null
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-sm shadow-md">
      <div className="mb-1 font-medium text-popover-foreground">{formatTimestamp(point.payload.captured_at)}</div>
      <div className="tabular-nums text-muted-foreground">
        <span className="font-medium text-popover-foreground">{point.value.toLocaleString()}</span> concurrent players
      </div>
    </div>
  )
}

export function CcuIntradayChart({ history }: { history: CcuSnapshotPoint[] }) {
  if (history.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        No intraday CCU data for this game yet.
      </p>
    )
  }

  const data = history.map((point) => ({
    captured_at: point.captured_at,
    ccu: point.concurrent_players,
  }))

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="captured_at"
          tickFormatter={formatTimestamp}
          tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
          axisLine={{ stroke: "var(--border)" }}
          tickLine={false}
        />
        <YAxis
          tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
          axisLine={false}
          tickLine={false}
          width={48}
        />
        <Tooltip content={<ChartTooltip />} />
        <Line
          type="monotone"
          dataKey="ccu"
          stroke={SERIES_CCU}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
