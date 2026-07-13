-- Migration 016: ccu_snapshots table for intraday (hourly) CCU history.
--
-- player_metrics has a unique(game_id, date) constraint and is written once
-- per weekly pipeline run -- one data point per game per week. Steam's
-- official API (ISteamUserStats/GetNumberOfCurrentPlayers) only ever
-- returns the *current* value, no history, so higher-resolution CCU data
-- requires the platform to poll on its own schedule. This table holds those
-- raw timestamped snapshots, collected hourly by
-- scripts/collect_ccu_snapshot.py (.github/workflows/ccu_hourly.yml) --
-- separate from player_metrics so nothing that assumes one row per game per
-- day (financial_overlay/health_score.py, the dashboard's daily signal
-- cards) has its semantics changed.
--
-- Combines table creation + the anon SELECT policy in one migration (unlike
-- 011-013, which retrofitted policies onto already-existing tables) since
-- this table is dashboard-facing from day one (Game Signals detail page
-- intraday CCU chart).
--
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create table if not exists ccu_snapshots (
  id                 uuid primary key default gen_random_uuid(),
  game_id            uuid references games(game_id),
  captured_at        timestamptz not null default now(),
  concurrent_players integer,
  created_at         timestamptz default now()
);

create index if not exists ccu_snapshots_game_captured_idx
  on ccu_snapshots (game_id, captured_at desc);

alter table ccu_snapshots enable row level security;

create policy "anon_select_ccu_snapshots" on ccu_snapshots
  for select to anon using (true);
