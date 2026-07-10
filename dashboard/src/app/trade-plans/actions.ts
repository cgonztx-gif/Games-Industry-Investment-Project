"use server"

import { revalidatePath } from "next/cache"

import { createServiceRoleClient } from "@/lib/supabase/server"

type ActionResult = { success: boolean; error?: string }

// This module only ever writes `status` (and `reviewed_at` on plans) in
// Supabase. It must never call Alpaca or place an order -- that remains
// agents/portfolio/execution_agent.py's job, unchanged, which only acts on
// trade_orders rows already at status='approved' and re-checks that status
// itself as the hard guard before calling Alpaca (CLAUDE.md Key Architecture
// Rules 4/5). This is the web-UI equivalent of scripts/review_trade_plans.py.

async function reviewPlan(
  planId: string,
  decision: "approved" | "rejected"
): Promise<ActionResult> {
  if (!planId) {
    return { success: false, error: "Missing plan id" }
  }

  const supabase = createServiceRoleClient()

  // Re-read from Supabase rather than trusting a client-supplied status --
  // same "derive identity/state from a trusted source, not the caller" rule
  // the Alpaca execution guard follows (CLAUDE.md Key Architecture Rule 5).
  const { data: plan, error: fetchError } = await supabase
    .from("trade_plans")
    .select("plan_id, status")
    .eq("plan_id", planId)
    .single()

  if (fetchError || !plan) {
    return { success: false, error: "Trade plan not found" }
  }
  if (plan.status !== "pending") {
    return { success: false, error: `Trade plan is already ${plan.status}` }
  }

  // The status condition here (not just plan_id) makes this UPDATE the actual
  // atomic guard -- the read above can't prevent a concurrent request from
  // reviewing the same plan between the read and this write, so the write
  // itself must only succeed against a still-pending row. Zero rows updated
  // means we lost that race.
  const { data: updatedPlan, error: updateError } = await supabase
    .from("trade_plans")
    .update({ status: decision, reviewed_at: new Date().toISOString() })
    .eq("plan_id", planId)
    .eq("status", "pending")
    .select("plan_id")

  if (updateError) {
    return { success: false, error: updateError.message }
  }
  if (!updatedPlan || updatedPlan.length === 0) {
    return { success: false, error: "Trade plan was just reviewed by someone else" }
  }

  // Cascade to every currently-pending order under this plan -- orders
  // already approved/rejected/filled/cancelled are left untouched. Mirrors
  // scripts/review_trade_plans.py's _apply_plan_status().
  const { error: cascadeError } = await supabase
    .from("trade_orders")
    .update({ status: decision })
    .eq("plan_id", planId)
    .eq("status", "pending")

  if (cascadeError) {
    return {
      success: false,
      error: `Plan ${decision}, but failed to cascade to its orders: ${cascadeError.message}`,
    }
  }

  revalidatePath("/trade-plans")
  return { success: true }
}

async function reviewOrder(
  orderId: string,
  decision: "approved" | "rejected"
): Promise<ActionResult> {
  if (!orderId) {
    return { success: false, error: "Missing order id" }
  }

  const supabase = createServiceRoleClient()

  const { data: order, error: fetchError } = await supabase
    .from("trade_orders")
    .select("order_id, status")
    .eq("order_id", orderId)
    .single()

  if (fetchError || !order) {
    return { success: false, error: "Trade order not found" }
  }
  if (order.status !== "pending") {
    return { success: false, error: `Trade order is already ${order.status}` }
  }

  // Same atomic-guard reasoning as reviewPlan's UPDATE above.
  const { data: updatedOrder, error: updateError } = await supabase
    .from("trade_orders")
    .update({ status: decision })
    .eq("order_id", orderId)
    .eq("status", "pending")
    .select("order_id")

  if (updateError) {
    return { success: false, error: updateError.message }
  }
  if (!updatedOrder || updatedOrder.length === 0) {
    return { success: false, error: "Trade order was just reviewed by someone else" }
  }

  revalidatePath("/trade-plans")
  return { success: true }
}

export async function approveTradePlan(planId: string): Promise<ActionResult> {
  return reviewPlan(planId, "approved")
}

export async function rejectTradePlan(planId: string): Promise<ActionResult> {
  return reviewPlan(planId, "rejected")
}

export async function approveTradeOrder(orderId: string): Promise<ActionResult> {
  return reviewOrder(orderId, "approved")
}

export async function rejectTradeOrder(orderId: string): Promise<ActionResult> {
  return reviewOrder(orderId, "rejected")
}
