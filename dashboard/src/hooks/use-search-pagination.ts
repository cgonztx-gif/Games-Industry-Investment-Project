"use client"

import * as React from "react"

// Shared client-side search+pagination logic -- originally hand-rolled
// identically in signals-grid.tsx and trade-history-table.tsx (same state
// shape, filter memo, page-clamping math, and Previous/Next semantics),
// extracted here so a fix to the algorithm only needs to happen once.
export function useSearchPagination<T>(
  items: T[],
  matches: (item: T, query: string) => boolean,
  pageSize: number
) {
  const [query, setQueryState] = React.useState("")
  const [page, setPage] = React.useState(0)

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((item) => matches(item, q))
  }, [items, query, matches])

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const clampedPage = Math.min(page, pageCount - 1)
  const paged = filtered.slice(clampedPage * pageSize, clampedPage * pageSize + pageSize)

  function setQuery(value: string) {
    setQueryState(value)
    setPage(0)
  }

  return { query, setQuery, filtered, paged, pageCount, clampedPage, setPage }
}
