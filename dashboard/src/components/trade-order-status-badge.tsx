import { Badge } from "@/components/ui/badge"
import { STATUS_GOOD } from "@/lib/status-colors"

// Distinct from STATUS_GOOD -- "approved" is a different workflow state than
// "filled" and must not read as the same color (categorical slot 1, dark
// surface step; see the plan file's palette report).
const APPROVED_COLOR = "#3987e5"

// Single canonical trade_orders.status -> color mapping, shared by
// trade-plan-card.tsx and trade-history-table.tsx so the same order can't
// read as two different severities depending on which page shows it.
export function TradeOrderStatusBadge({ status }: { status: string }) {
  if (status === "filled") {
    return (
      <Badge
        variant="outline"
        className="border-[#22c55e]/30 bg-[#22c55e]/10"
        style={{ color: STATUS_GOOD }}
      >
        filled
      </Badge>
    )
  }
  if (status === "approved") {
    return (
      <Badge
        variant="outline"
        className="border-[#3987e5]/30 bg-[#3987e5]/10"
        style={{ color: APPROVED_COLOR }}
      >
        approved
      </Badge>
    )
  }
  if (status === "rejected" || status === "cancelled") {
    return <Badge variant="destructive">{status}</Badge>
  }
  return <Badge variant="secondary">{status}</Badge>
}
