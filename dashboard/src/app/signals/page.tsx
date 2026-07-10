import { SignalsGrid } from "@/components/signals-grid"
import { getSignalCards } from "@/lib/data/signals"

// Signal freshness changes with every weekly worker run -- must never serve a
// build-time snapshot (same bug this dashboard already hit once, see tasks.md).
export const dynamic = "force-dynamic"

export default async function SignalsPage() {
  const cards = await getSignalCards()

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Game Signals</h1>
        <p className="text-sm text-muted-foreground">
          CCU trend, community sentiment, and patch cadence for every active watchlist game
          with at least one signal in the last 90 days.
        </p>
      </div>

      {cards.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No games have received player-count, sentiment, or patch data in the last 90 days yet.
        </p>
      ) : (
        <SignalsGrid cards={cards} />
      )}
    </div>
  )
}
