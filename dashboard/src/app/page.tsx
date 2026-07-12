import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { PortfolioReturnChart } from "@/components/portfolio-return-chart"
import { PositionBreakdownChart } from "@/components/position-breakdown-chart"
import { getCurrentPositions, getPortfolioSnapshots } from "@/lib/data/portfolio"
import { pnlClass } from "@/lib/status-colors"

// Live account/position state -- must never serve a build-time snapshot.
export const dynamic = "force-dynamic"

function formatUsd(value: number | null | undefined) {
  if (value === null || value === undefined) return "—"
  return value.toLocaleString(undefined, { style: "currency", currency: "USD" })
}

function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined) return "—"
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`
}

function StatRow({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border pb-3 last:border-b-0 last:pb-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={`text-sm font-semibold tabular-nums ${valueClassName ?? ""}`}>{value}</span>
    </div>
  )
}

export default async function PortfolioOverviewPage() {
  const [positions, snapshots] = await Promise.all([
    getCurrentPositions(),
    getPortfolioSnapshots(),
  ])

  const latest = snapshots.at(-1) ?? null

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio Overview</h1>
        <p className="text-sm text-muted-foreground">
          Current Alpaca paper-trading positions and cumulative return vs. the S&P 500.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardDescription>Total Value</CardDescription>
              <CardTitle className="text-3xl tabular-nums">
                {formatUsd(latest?.total_value ?? null)}
              </CardTitle>
              <p className={`text-sm font-medium tabular-nums ${pnlClass(latest?.total_return_pct ?? null)}`}>
                {formatPct(latest?.total_return_pct ?? null)} since inception
              </p>
            </div>
            {snapshots.length > 0 && (
              <div className="flex flex-col items-end gap-1.5 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-4 rounded-full bg-primary" />
                  Portfolio
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="inline-block w-4"
                    style={{ borderTop: "2px dashed #71717a" }}
                  />
                  S&P 500
                </span>
              </div>
            )}
          </CardHeader>
          <CardContent>
            {snapshots.length === 0 ? (
              <p className="py-12 text-center text-sm text-muted-foreground">
                No portfolio snapshots yet — the Returns Tracker writes one after the first
                weekly pipeline run with an Alpaca account fetch.
              </p>
            ) : (
              <PortfolioReturnChart snapshots={snapshots} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Account</CardTitle>
            <CardDescription>As of the latest snapshot</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <StatRow label="Cash" value={formatUsd(latest?.cash ?? null)} />
            <StatRow
              label="Total Return"
              value={formatPct(latest?.total_return_pct ?? null)}
              valueClassName={pnlClass(latest?.total_return_pct ?? null)}
            />
            <StatRow
              label="S&P 500 (same window)"
              value={formatPct(latest?.benchmark_return_pct ?? null)}
              valueClassName={pnlClass(latest?.benchmark_return_pct ?? null)}
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Portfolio Composition</CardTitle>
          <CardDescription>
            Share of total portfolio value held in each open position vs. cash, as of
            the latest snapshot.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {latest === null || latest.total_value === null || latest.total_value <= 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No portfolio snapshot yet — composition appears once the Returns Tracker
              records a total portfolio value.
            </p>
          ) : (
            <PositionBreakdownChart
              positions={positions}
              totalValue={latest.total_value}
              cash={latest.cash ?? 0}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Current Positions</CardTitle>
          <CardDescription>
            Mirrors Alpaca&apos;s live open positions as of the last Returns Tracker run.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {positions.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No open positions yet — no approved trade orders have filled.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Ticker</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg Entry</TableHead>
                  <TableHead className="text-right">Current Price</TableHead>
                  <TableHead className="text-right">Unrealized P&amp;L</TableHead>
                  <TableHead className="text-right">As Of</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {positions.map((p) => (
                  <TableRow key={p.position_id}>
                    <TableCell className="font-medium">{p.ticker}</TableCell>
                    <TableCell className="text-right tabular-nums">{p.qty ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatUsd(p.avg_entry_price)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatUsd(p.current_price)}
                    </TableCell>
                    <TableCell className={`text-right tabular-nums ${pnlClass(p.unrealized_pnl)}`}>
                      {formatUsd(p.unrealized_pnl)}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {new Date(p.as_of).toLocaleDateString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
