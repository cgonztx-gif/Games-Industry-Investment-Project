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
import { getCurrentPositions, getPortfolioSnapshots } from "@/lib/data/portfolio"

// Live account/position state -- must never serve a build-time snapshot.
export const dynamic = "force-dynamic"

function formatUsd(value: number | null) {
  if (value === null || value === undefined) return "—"
  return value.toLocaleString(undefined, { style: "currency", currency: "USD" })
}

function formatPct(value: number | null) {
  if (value === null || value === undefined) return "—"
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`
}

function pnlClass(value: number | null) {
  if (value === null || value === undefined) return "text-muted-foreground"
  if (value > 0) return "text-[#006300] dark:text-[#0ca30c]"
  if (value < 0) return "text-[#d03b3b]"
  return "text-muted-foreground"
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

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total Value</CardDescription>
            <CardTitle className="text-2xl tabular-nums">
              {formatUsd(latest?.total_value ?? null)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Cash</CardDescription>
            <CardTitle className="text-2xl tabular-nums">
              {formatUsd(latest?.cash ?? null)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total Return (since inception)</CardDescription>
            <CardTitle className={`text-2xl tabular-nums ${pnlClass(latest?.total_return_pct ?? null)}`}>
              {formatPct(latest?.total_return_pct ?? null)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>S&P 500 (same window)</CardDescription>
            <CardTitle className={`text-2xl tabular-nums ${pnlClass(latest?.benchmark_return_pct ?? null)}`}>
              {formatPct(latest?.benchmark_return_pct ?? null)}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Cumulative Return vs. Benchmark</CardTitle>
          <CardDescription>
            Both series are cumulative since the first recorded snapshot, not week-over-week.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {snapshots.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No portfolio snapshots yet — the Returns Tracker writes one after the first
              weekly pipeline run with an Alpaca account fetch.
            </p>
          ) : (
            <>
              <div className="mb-4 flex items-center gap-4 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-4 rounded-full bg-[#2a78d6]" />
                  Portfolio
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="inline-block w-4"
                    style={{ borderTop: "2px dashed #898781" }}
                  />
                  S&P 500
                </span>
              </div>
              <PortfolioReturnChart snapshots={snapshots} />
            </>
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
