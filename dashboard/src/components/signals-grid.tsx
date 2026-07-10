"use client"

import * as React from "react"

import { Input } from "@/components/ui/input"
import { PaginationControls } from "@/components/pagination-controls"
import { SignalCard } from "@/components/signal-card"
import { useSearchPagination } from "@/hooks/use-search-pagination"
import type { SignalCard as SignalCardData } from "@/lib/data/signals"

const PAGE_SIZE = 24

const matchesTitle = (card: SignalCardData, q: string) => card.title.toLowerCase().includes(q)

export function SignalsGrid({ cards }: { cards: SignalCardData[] }) {
  const { query, setQuery, filtered, paged, pageCount, clampedPage, setPage } =
    useSearchPagination(cards, matchesTitle, PAGE_SIZE)

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <Input
          placeholder="Search by title..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
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

      <PaginationControls
        clampedPage={clampedPage}
        pageCount={pageCount}
        onPrevious={() => setPage(clampedPage - 1)}
        onNext={() => setPage(clampedPage + 1)}
      />
    </div>
  )
}
