import { ProposalCard } from "@/components/proposal-card"
import { getPendingProposals } from "@/lib/data/watchlist-proposals"

// Pending proposals change on every Discovery run and every approve/reject --
// must never serve a build-time snapshot.
export const dynamic = "force-dynamic"

export default async function WatchlistProposalsPage() {
  const proposals = await getPendingProposals()

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist Proposals</h1>
        <p className="text-sm text-muted-foreground">
          Candidates the Discovery agent surfaced for review. Approving activates the
          matching watchlist row if one already exists.
        </p>
      </div>

      {proposals.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No pending proposals — the Discovery agent has nothing new to review right now.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {proposals.map((proposal) => (
            <ProposalCard key={proposal.proposal_id} proposal={proposal} />
          ))}
        </div>
      )}
    </div>
  )
}
