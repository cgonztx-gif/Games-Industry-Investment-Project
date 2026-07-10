-- Migration 013: SELECT-only RLS policy for the anon role on the base
-- `watchlist` table, for the per-game signal cards page
-- (dashboard/src/app/signals/page.tsx).
--
-- Found live 2026-07-10 while testing migration 012 against the running dev
-- server: the /signals page scopes eligible games to `watchlist.active =
-- true` (dashboard/src/lib/data/signals.ts::getActiveWatchlistGameIds()),
-- but neither migration 011 nor 012 granted anon SELECT on `watchlist`
-- itself -- 011 only covered `watchlist_proposals` (a different table: the
-- Discovery agent's proposal queue, not the active watchlist). Confirmed
-- live: service_role/anon row counts were 4017/0 before this migration,
-- which silently produced zero "eligible" games on /signals (RLS filters
-- rows out with a 200, not an error -- same failure shape as every prior
-- RLS gap in this repo). This grants read access to exactly that one table.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create policy "anon_select_watchlist" on watchlist
  for select to anon using (true);
