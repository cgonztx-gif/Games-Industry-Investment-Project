import { SidebarTrigger } from "@/components/ui/sidebar"
import { Separator } from "@/components/ui/separator"
import { pnlClass } from "@/lib/status-colors"
import type { PortfolioSnapshot } from "@/lib/data/portfolio"

function formatUsd(value: number | null | undefined) {
  if (value === null || value === undefined) return "—"
  return value.toLocaleString(undefined, { style: "currency", currency: "USD" })
}

function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined) return "—"
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`
}

// A persistent, always-visible portfolio snapshot chip -- the one piece of
// global state worth surfacing on every route, mirroring the reference
// image's ever-present balance readout.
export function SiteHeader({ snapshot }: { snapshot: PortfolioSnapshot | null }) {
  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-border bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="flex items-center gap-2">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
      </div>
      {snapshot && (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-1.5">
          <div className="text-right">
            <div className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              Total Value
            </div>
            <div className="text-sm font-semibold tabular-nums">
              {formatUsd(snapshot.total_value)}
            </div>
          </div>
          <Separator orientation="vertical" className="h-6" />
          <div className="text-right">
            <div className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              Return
            </div>
            <div className={`text-sm font-semibold tabular-nums ${pnlClass(snapshot.total_return_pct)}`}>
              {formatPct(snapshot.total_return_pct)}
            </div>
          </div>
        </div>
      )}
    </header>
  )
}
