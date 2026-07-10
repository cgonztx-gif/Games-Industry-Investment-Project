-- Migration 011: SELECT-only RLS policies for the anon role, for the Next.js
-- dashboard's read-only browser/Server-Component client (dashboard/src/lib/supabase/client.ts).
--
-- Confirmed live 2026-07-10: RLS is enabled on every table with zero existing
-- policies, so the anon key currently gets an empty result set back from
-- every table (not an error -- PostgREST just filters all rows out). This
-- grants read access to exactly the tables Phase 7's first two dashboard
-- pages query: Portfolio Overview (positions, portfolio_snapshots) and the
-- Watchlist Proposals review queue (watchlist_proposals, joined to games and
-- studios). Every dashboard WRITE still goes through a Server Action using
-- the service_role key, which bypasses RLS entirely and never touches these
-- anon policies -- see dashboard/src/app/watchlist-proposals/actions.ts.
--
-- Deliberately scoped to only these 5 tables for this pass; the remaining
-- tables a future dashboard page reads (player_metrics, sentiment_snapshots,
-- patch_events, studio_signals, equity_signals, weekly_briefings, trade_plans,
-- trade_orders, news_items) get their own anon SELECT policy when that page
-- is built, not preemptively here.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create policy "anon_select_positions" on positions
  for select to anon using (true);

create policy "anon_select_portfolio_snapshots" on portfolio_snapshots
  for select to anon using (true);

create policy "anon_select_watchlist_proposals" on watchlist_proposals
  for select to anon using (true);

create policy "anon_select_games" on games
  for select to anon using (true);

create policy "anon_select_studios" on studios
  for select to anon using (true);
