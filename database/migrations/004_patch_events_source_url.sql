-- Migration 004: source URL for idempotent patch-event ingestion.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

alter table patch_events
  add column if not exists source_url text;

create unique index if not exists uq_patch_events_game_source_url
  on patch_events (game_id, source_url)
  where source_url is not null;
