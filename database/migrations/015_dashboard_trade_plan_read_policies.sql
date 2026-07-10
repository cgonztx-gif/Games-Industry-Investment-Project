-- Migration 015: SELECT-only RLS policies for the anon role on `trade_plans`
-- and `trade_orders`, for the new Trade Plan Approval UI
-- (dashboard/src/app/trade-plans/page.tsx).
--
-- Both tables were explicitly called out in migration 011's own comment as
-- deferred until the page that reads them gets built -- this is that page.
-- As with every prior write path in this dashboard, the anon policy here is
-- READ-only: approve/reject writes go through a Server Action using the
-- service_role key (dashboard/src/app/trade-plans/actions.ts), which bypasses
-- RLS entirely and never touches this policy -- see
-- dashboard/src/app/watchlist-proposals/actions.ts for the precedent.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create policy "anon_select_trade_plans" on trade_plans
  for select to anon using (true);

create policy "anon_select_trade_orders" on trade_orders
  for select to anon using (true);
