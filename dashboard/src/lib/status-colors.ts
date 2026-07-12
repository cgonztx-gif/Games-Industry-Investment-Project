// Single source of truth for the app's status/brand palette (dataviz skill,
// references/palette.md, validated against this theme's #0a0a0a/#131313
// dark surfaces -- see the plan file for the contrast report). Previously
// duplicated as local STATUS_GOOD/WARNING/CRITICAL/NEUTRAL constants across
// signal-card.tsx, briefing-detail.tsx, trade-order-status-badge.tsx, and as
// standalone hex in page.tsx's pnlClass / trade-history-table.tsx's
// actionClass -- import from here instead so a status color can't drift to a
// different value on one page than another.
export const STATUS_GOOD = "#22c55e" // brand accent, doubles as "positive"
export const STATUS_WARNING = "#fab219"
export const STATUS_SERIOUS = "#ec835a"
export const STATUS_CRITICAL = "#d03b3b"
export const STATUS_NEUTRAL = "#71717a"

export const BRAND = STATUS_GOOD

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-muted-foreground"
  if (value > 0) return "text-[#22c55e]"
  if (value < 0) return "text-[#d03b3b]"
  return "text-muted-foreground"
}
