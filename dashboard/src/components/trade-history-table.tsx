"use client"

import type { CSSProperties } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PaginationControls } from "@/components/pagination-controls"
import { TradeOrderStatusBadge } from "@/components/trade-order-status-badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useSearchPagination } from "@/hooks/use-search-pagination"
import type { TradeHistoryOrder } from "@/lib/data/trade-history"
import { STATUS_GOOD, STATUS_CRITICAL } from "@/lib/status-colors"

const PAGE_SIZE = 25

const matchesTicker = (order: TradeHistoryOrder, q: string) =>
  order.ticker.toLowerCase().includes(q)

function formatWeekOf(weekOf: string): string {
  return new Date(`${weekOf}T00:00:00Z`).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  })
}

function formatDateTime(value: string | null): string {
  if (!value) return "—"
  return new Date(value).toLocaleString()
}

function formatSizeUsd(sizeUsd: number | null): string {
  if (sizeUsd === null) return "—"
  return sizeUsd.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  })
}

// Buy/sell read like a gain/loss signal (status-colors.ts), hold is neutral.
function actionStyle(action: string): CSSProperties | undefined {
  if (action === "buy") return { color: STATUS_GOOD }
  if (action === "sell") return { color: STATUS_CRITICAL }
  return undefined
}

function RationaleDialog({ order }: { order: TradeHistoryOrder }) {
  const rationale = order.trade_plans?.claude_rationale
  if (!rationale) {
    return <span className="text-sm text-muted-foreground">—</span>
  }
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          View rationale
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {order.ticker} — {order.trade_plans ? formatWeekOf(order.trade_plans.week_of) : ""}{" "}
            plan rationale
          </DialogTitle>
          <DialogDescription>
            The Portfolio Manager reasons about the whole weekly plan at once, so every order
            under this plan shares this same rationale.
          </DialogDescription>
        </DialogHeader>
        <p className="max-h-[60vh] overflow-y-auto text-sm whitespace-pre-wrap">{rationale}</p>
      </DialogContent>
    </Dialog>
  )
}

export function TradeHistoryTable({ orders }: { orders: TradeHistoryOrder[] }) {
  const { query, setQuery, filtered, paged, pageCount, clampedPage, setPage } =
    useSearchPagination(orders, matchesTicker, PAGE_SIZE)

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <Input
          placeholder="Search by ticker..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="max-w-xs"
        />
        <p className="text-sm text-muted-foreground">
          {filtered.length} order{filtered.length === 1 ? "" : "s"}
        </p>
      </div>

      {paged.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No trade orders match &quot;{query}&quot;.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Ticker</TableHead>
              <TableHead>Action</TableHead>
              <TableHead className="text-right">Size</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Week Of</TableHead>
              <TableHead>Created At</TableHead>
              <TableHead>Filled At</TableHead>
              <TableHead>Alpaca Order ID</TableHead>
              <TableHead className="text-right">Rationale</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.map((order) => (
              <TableRow key={order.order_id}>
                <TableCell className="font-medium">{order.ticker}</TableCell>
                <TableCell className="font-medium capitalize" style={actionStyle(order.action)}>
                  {order.action}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatSizeUsd(order.size_usd)}
                </TableCell>
                <TableCell>
                  <TradeOrderStatusBadge status={order.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {order.trade_plans ? formatWeekOf(order.trade_plans.week_of) : "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDateTime(order.created_at)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDateTime(order.filled_at)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {order.alpaca_order_id ?? "—"}
                </TableCell>
                <TableCell className="text-right">
                  <RationaleDialog order={order} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <PaginationControls
        clampedPage={clampedPage}
        pageCount={pageCount}
        onPrevious={() => setPage(clampedPage - 1)}
        onNext={() => setPage(clampedPage + 1)}
      />
    </div>
  )
}
