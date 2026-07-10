-- Migration 012: SELECT-only RLS policies for the anon role, for the
-- per-game signal cards page (dashboard/src/app/signals/page.tsx).
--
-- Confirmed live 2026-07-10: same pattern as migration 011 -- RLS is enabled
-- on player_metrics, sentiment_snapshots, and patch_events with zero existing
-- policies, so the anon key currently gets an empty result set back from all
-- three (service_role vs. anon count comparison: player_metrics 1854/0,
-- sentiment_snapshots 972/0, patch_events 794/0). This grants read access to
-- exactly those 3 tables -- the ones migration 011 explicitly deferred.
-- Every dashboard WRITE still goes through a Server Action using the
-- service_role key, which bypasses RLS entirely; this page is read-only.
--
-- Remaining tables (studio_signals, equity_signals, weekly_briefings,
-- trade_plans, trade_orders, news_items) still get their own anon SELECT
-- policy when the page that reads them is built, not preemptively here.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create policy "anon_select_player_metrics" on player_metrics
  for select to anon using (true);

create policy "anon_select_sentiment_snapshots" on sentiment_snapshots
  for select to anon using (true);

create policy "anon_select_patch_events" on patch_events
  for select to anon using (true);
