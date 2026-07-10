import { TradeHistoryTable } from "@/components/trade-history-table"
import { getTradeHistory } from "@/lib/data/trade-history"

// Every filled/approved/rejected order accumulates forever -- must never
// serve a build-time snapshot.
export const dynamic = "force-dynamic"

export default async function TradeHistoryPage() {
  const orders = await getTradeHistory()

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Trade History</h1>
        <p className="text-sm text-muted-foreground">
          Every trade order ever proposed, across all statuses, with the original Claude
          rationale from its parent trade plan.
        </p>
      </div>

      {orders.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No trade orders yet — the Execution Agent writes one here after a trade plan is
          approved.
        </p>
      ) : (
        <TradeHistoryTable orders={orders} />
      )}
    </div>
  )
}
