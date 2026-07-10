"use client"

import * as React from "react"
import { useTransition } from "react"
import { CheckIcon, XIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
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
import { TradeOrderStatusBadge } from "@/components/trade-order-status-badge"
import type { PendingTradePlan } from "@/lib/data/trade-plans"
import {
  approveTradeOrder,
  approveTradePlan,
  rejectTradeOrder,
  rejectTradePlan,
} from "@/app/trade-plans/actions"

function formatWeekOf(weekOf: string): string {
  return new Date(`${weekOf}T00:00:00Z`).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  })
}

function formatSizeUsd(sizeUsd: number | null): string {
  if (sizeUsd === null) return "—"
  return sizeUsd.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  })
}

export function TradePlanCard({ plan }: { plan: PendingTradePlan }) {
  const [isPlanPending, startPlanTransition] = useTransition()
  const [isOrderPending, startOrderTransition] = useTransition()
  const [resolved, setResolved] = React.useState(false)

  function handlePlanDecision(decision: "approve" | "reject") {
    startPlanTransition(async () => {
      const action = decision === "approve" ? approveTradePlan : rejectTradePlan
      const result = await action(plan.plan_id)
      if (result.success) {
        toast.success(`Plan for ${formatWeekOf(plan.week_of)} ${decision === "approve" ? "approved" : "rejected"}`)
        setResolved(true)
      } else {
        toast.error(result.error ?? "Something went wrong")
      }
    })
  }

  function handleOrderDecision(orderId: string, ticker: string, decision: "approve" | "reject") {
    startOrderTransition(async () => {
      const action = decision === "approve" ? approveTradeOrder : rejectTradeOrder
      const result = await action(orderId)
      if (result.success) {
        toast.success(`${ticker} order ${decision === "approve" ? "approved" : "rejected"}`)
        // No local status override -- revalidatePath("/trade-plans") inside
        // the Server Action refreshes this component's `plan` prop with the
        // real DB state, which effectiveStatus reads directly below. A local
        // shadow map would otherwise permanently mask a later status change
        // (e.g. another reviewer, or the execution agent filling the order).
      } else {
        toast.error(result.error ?? "Something went wrong")
      }
    })
  }

  if (resolved) return null

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{formatWeekOf(plan.week_of)}</CardTitle>
            <CardDescription>Portfolio Manager trade plan</CardDescription>
          </div>
          <Badge variant="secondary">pending</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {plan.claude_rationale && (
          <div>
            <div className="text-xs font-medium text-muted-foreground">Rationale</div>
            <p className="text-sm">{plan.claude_rationale}</p>
          </div>
        )}

        {plan.trade_orders.length === 0 ? (
          <p className="text-sm text-muted-foreground">No trade orders under this plan.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Override</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plan.trade_orders.map((order) => {
                const isOverridable = order.status === "pending"
                return (
                  <TableRow key={order.order_id}>
                    <TableCell className="font-medium">{order.ticker}</TableCell>
                    <TableCell className="capitalize">{order.action}</TableCell>
                    <TableCell>{formatSizeUsd(order.size_usd)}</TableCell>
                    <TableCell>
                      <TradeOrderStatusBadge status={order.status} />
                    </TableCell>
                    <TableCell className="text-right">
                      {isOverridable && (
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={isOrderPending}
                            onClick={() =>
                              handleOrderDecision(order.order_id, order.ticker, "approve")
                            }
                          >
                            <CheckIcon />
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={isOrderPending}
                            onClick={() =>
                              handleOrderDecision(order.order_id, order.ticker, "reject")
                            }
                          >
                            <XIcon />
                            Reject
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <CardFooter className="gap-2">
        <Button size="sm" disabled={isPlanPending} onClick={() => handlePlanDecision("approve")}>
          <CheckIcon />
          Approve Plan
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={isPlanPending}
          onClick={() => handlePlanDecision("reject")}
        >
          <XIcon />
          Reject Plan
        </Button>
      </CardFooter>
    </Card>
  )
}
