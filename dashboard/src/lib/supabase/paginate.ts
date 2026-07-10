// PostgREST enforces its own server-side max-rows cap (confirmed live: a
// `.limit(5000)` request against the ~1854-row player_metrics table still
// came back truncated at 1000, silently) -- a single unpaginated request
// can't be trusted to return every row, so any potentially-large fetch pages
// via `.range()` instead of relying on `.limit()`. Shared by every data
// fetcher under `dashboard/src/lib/data/` that might read more than 1000 rows
// (originally built in `data/signals.ts`, extracted here for reuse).
const FETCH_PAGE_SIZE = 1000

// supabase-js types every PostgREST embed relation as an array, even a
// to-one embed guaranteed at most one row by its FK -- previously hand-rolled
// identically in watchlist-proposals.ts, sentiment-trend.ts, signals.ts, and
// trade-history.ts. Extracted here for the same reason fetchAllRows was.
export function normalizeToOne<T>(value: T | T[] | null | undefined): T | null {
  if (Array.isArray(value)) return value[0] ?? null
  return value ?? null
}

export async function fetchAllRows<T>(
  fetchPage: (from: number, to: number) => PromiseLike<{ data: T[] | null; error: { message: string } | null }>
): Promise<T[]> {
  const rows: T[] = []
  let offset = 0
  for (;;) {
    const { data, error } = await fetchPage(offset, offset + FETCH_PAGE_SIZE - 1)
    if (error) throw new Error(error.message)
    rows.push(...(data ?? []))
    if (!data || data.length < FETCH_PAGE_SIZE) break
    offset += FETCH_PAGE_SIZE
  }
  return rows
}
