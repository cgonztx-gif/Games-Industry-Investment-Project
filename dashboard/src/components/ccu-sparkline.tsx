"use client"

import { Line, LineChart, ResponsiveContainer, Tooltip } from "recharts"

import type { PlayerMetricPoint } from "@/lib/data/signals"

// Single series -- no legend box per the dataviz skill (the card title
// already names what's plotted); same categorical slot-1 blue as the
// Portfolio Overview chart (portfolio-return-chart.tsx) for consistency.
const SERIES_CCU = "#2a78d6"

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: { value: number | null; payload: { date: string } }[]
}) {
  if (!active || !payload?.length) return null
  const point = payload[0]
  if (point.value === null || point.value === undefined) return null
  return (
    <div className="rounded-md border bg-popover px-2 py-1 text-xs shadow-md">
      <div className="text-muted-foreground">
        {new Date(point.payload.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
      </div>
      <div className="font-medium tabular-nums text-popover-foreground">
        {point.value.toLocaleString()} CCU
      </div>
    </div>
  )
}

export function CcuSparkline({ history }: { history: PlayerMetricPoint[] }) {
  const data = history.map((point) => ({
    date: point.date,
    ccu: point.concurrent_players,
  }))

  return (
    <ResponsiveContainer width="100%" height={48}>
      <LineChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
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
