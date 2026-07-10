"use server"

import { revalidatePath } from "next/cache"

import { createServiceRoleClient } from "@/lib/supabase/server"

type ActionResult = { success: boolean; error?: string }

async function reviewProposal(
  proposalId: string,
  decision: "approved" | "rejected"
): Promise<ActionResult> {
  if (!proposalId) {
    return { success: false, error: "Missing proposal id" }
  }

  const supabase = createServiceRoleClient()

  // Re-read from Supabase rather than trusting a client-supplied status --
  // same "derive identity/state from a trusted source, not the caller" rule
  // the Alpaca execution guard follows (CLAUDE.md Key Architecture Rule 5).
  const { data: proposal, error: fetchError } = await supabase
    .from("watchlist_proposals")
    .select("proposal_id, game_id, status")
    .eq("proposal_id", proposalId)
    .single()

  if (fetchError || !proposal) {
    return { success: false, error: "Proposal not found" }
  }
  if (proposal.status !== "pending") {
    return { success: false, error: `Proposal is already ${proposal.status}` }
  }

  const { error: updateError } = await supabase
    .from("watchlist_proposals")
    .update({ status: decision, reviewed_at: new Date().toISOString() })
    .eq("proposal_id", proposalId)

  if (updateError) {
    return { success: false, error: updateError.message }
  }

  if (decision === "approved" && proposal.game_id) {
    // Only flips an existing watchlist row -- never creates one. A new
    // watchlist row for a brand-new game is a separate, not-yet-built step.
    const { error: watchlistError } = await supabase
      .from("watchlist")
      .update({ active: true })
      .eq("game_id", proposal.game_id)

    if (watchlistError) {
      return {
        success: false,
        error: `Proposal approved, but failed to activate watchlist row: ${watchlistError.message}`,
      }
    }
  }

  revalidatePath("/watchlist-proposals")
  return { success: true }
}

export async function approveProposal(proposalId: string): Promise<ActionResult> {
  return reviewProposal(proposalId, "approved")
}

export async function rejectProposal(proposalId: string): Promise<ActionResult> {
  return reviewProposal(proposalId, "rejected")
}
