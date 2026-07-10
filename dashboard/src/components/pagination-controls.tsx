import { Button } from "@/components/ui/button"

// Shared Previous/Next control row -- paired with useSearchPagination(),
// same JSX previously duplicated in signals-grid.tsx and trade-history-table.tsx.
export function PaginationControls({
  clampedPage,
  pageCount,
  onPrevious,
  onNext,
}: {
  clampedPage: number
  pageCount: number
  onPrevious: () => void
  onNext: () => void
}) {
  if (pageCount <= 1) return null

  return (
    <div className="flex items-center justify-center gap-3">
      <Button variant="outline" size="sm" disabled={clampedPage === 0} onClick={onPrevious}>
        Previous
      </Button>
      <p className="text-sm text-muted-foreground">
        Page {clampedPage + 1} of {pageCount}
      </p>
      <Button
        variant="outline"
        size="sm"
        disabled={clampedPage >= pageCount - 1}
        onClick={onNext}
      >
        Next
      </Button>
    </div>
  )
}
