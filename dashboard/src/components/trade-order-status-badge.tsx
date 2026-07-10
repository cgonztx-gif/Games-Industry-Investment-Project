import { Badge } from "@/components/ui/badge"

// Single canonical trade_orders.status -> color mapping, shared by
// trade-plan-card.tsx and trade-history-table.tsx so the same order can't
// read as two different severities depending on which page shows it.
export function TradeOrderStatusBadge({ status }: { status: string }) {
  if (status === "filled") {
    return (
      <Badge
        variant="outline"
        className="border-[#006300]/30 bg-[#006300]/10 text-[#006300] dark:border-[#0ca30c]/30 dark:bg-[#0ca30c]/10 dark:text-[#0ca30c]"
      >
        filled
      </Badge>
    )
  }
  if (status === "approved") {
    return (
      <Badge variant="outline" className="border-[#2a78d6]/30 bg-[#2a78d6]/10 text-[#2a78d6]">
        approved
      </Badge>
    )
  }
  if (status === "rejected" || status === "cancelled") {
    return <Badge variant="destructive">{status}</Badge>
  }
  return <Badge variant="secondary">{status}</Badge>
}
