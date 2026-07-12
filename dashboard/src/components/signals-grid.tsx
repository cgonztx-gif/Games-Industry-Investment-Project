"use client"

import * as React from "react"

import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { PaginationControls } from "@/components/pagination-controls"
import { SignalCard } from "@/components/signal-card"
import { useSearchPagination } from "@/hooks/use-search-pagination"
import type { SignalCard as SignalCardData } from "@/lib/data/signals"

const PAGE_SIZE = 24

const matchesTitle = (card: SignalCardData, q: string) => card.title.toLowerCase().includes(q)

const SORT_OPTIONS = {
  "title-asc": "Title (A–Z)",
  "title-desc": "Title (Z–A)",
  "ccu-desc": "CCU (high to low)",
  "ccu-asc": "CCU (low to high)",
  "sentiment-desc": "Sentiment (high to low)",
  "sentiment-asc": "Sentiment (low to high)",
} as const

type SortOption = keyof typeof SORT_OPTIONS

// Missing values always sort to the end regardless of direction -- "no data"
// isn't meaningfully high or low, so it shouldn't get sorted to the top just
// because a descending sort treats absence as -Infinity.
function compareNullable(a: number | null, b: number | null, direction: 1 | -1): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return (a - b) * direction
}

function sortCards(cards: SignalCardData[], sort: SortOption): SignalCardData[] {
  const sorted = cards.slice()
  switch (sort) {
    case "title-asc":
      return sorted.sort((a, b) => a.title.localeCompare(b.title))
    case "title-desc":
      return sorted.sort((a, b) => b.title.localeCompare(a.title))
    case "ccu-desc":
      return sorted.sort((a, b) =>
        compareNullable(
          a.latest_player_metric?.concurrent_players ?? null,
          b.latest_player_metric?.concurrent_players ?? null,
          -1
        )
      )
    case "ccu-asc":
      return sorted.sort((a, b) =>
        compareNullable(
          a.latest_player_metric?.concurrent_players ?? null,
          b.latest_player_metric?.concurrent_players ?? null,
          1
        )
      )
    case "sentiment-desc":
      return sorted.sort((a, b) => compareNullable(a.sentiment_score, b.sentiment_score, -1))
    case "sentiment-asc":
      return sorted.sort((a, b) => compareNullable(a.sentiment_score, b.sentiment_score, 1))
  }
}

export function SignalsGrid({ cards }: { cards: SignalCardData[] }) {
  const [sort, setSort] = React.useState<SortOption>("title-asc")
  const sortedCards = React.useMemo(() => sortCards(cards, sort), [cards, sort])

  const { query, setQuery, filtered, paged, pageCount, clampedPage, setPage } =
    useSearchPagination(sortedCards, matchesTitle, PAGE_SIZE)

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="Search by title..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="max-w-xs"
          />
          <Select value={sort} onValueChange={(value) => setSort(value as SortOption)}>
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="Sort by..." />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(SORT_OPTIONS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
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
