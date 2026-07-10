"use client"

import * as React from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SignalCard } from "@/components/signal-card"
import type { SignalCard as SignalCardData } from "@/lib/data/signals"

const PAGE_SIZE = 24

export function SignalsGrid({ cards }: { cards: SignalCardData[] }) {
  const [query, setQuery] = React.useState("")
  const [page, setPage] = React.useState(0)

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return cards
    return cards.filter((card) => card.title.toLowerCase().includes(q))
  }, [cards, query])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const clampedPage = Math.min(page, pageCount - 1)
  const paged = filtered.slice(clampedPage * PAGE_SIZE, clampedPage * PAGE_SIZE + PAGE_SIZE)

  function handleQueryChange(value: string) {
    setQuery(value)
    setPage(0)
  }

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <Input
          placeholder="Search by title..."
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          className="max-w-xs"
        />
        <p className="text-sm text-muted-foreground">
          {filtered.length} game{filtered.length === 1 ? "" : "s"}
        </p>
      </div>

      {paged.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No games match &quot;{query}&quot;.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {paged.map((card) => (
            <SignalCard key={card.game_id} card={card} />
          ))}
        </div>
      )}

      {pageCount > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={clampedPage === 0}
            onClick={() => setPage(clampedPage - 1)}
          >
            Previous
          </Button>
          <p className="text-sm text-muted-foreground">
            Page {clampedPage + 1} of {pageCount}
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={clampedPage >= pageCount - 1}
            onClick={() => setPage(clampedPage + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  )
}
