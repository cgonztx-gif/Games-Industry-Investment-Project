-- Migration 014: SELECT-only RLS policy for the anon role on `weekly_briefings`,
-- for the new Weekly Briefings feed page (dashboard/src/app/briefings/page.tsx).
--
-- `weekly_briefings` was explicitly called out in migration 011's own comment
-- as one of the tables deferred until the page that reads it gets built --
-- this is that page. `games` and `studios` (used to resolve game_id/studio_id
-- references inside top_opportunities/risk_flags JSONB) are already covered
-- by migration 011; not re-granted here.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create policy "anon_select_weekly_briefings" on weekly_briefings
  for select to anon using (true);
