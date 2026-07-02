-- Migration 006: cadence baseline + monetization-without-content flags for patch_events.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

alter table patch_events
  add column if not exists cadence_status text
    check (cadence_status in ('baseline', 'on_pace', 'slowing', 'absent')),
  add column if not exists cadence_baseline_days integer,
  add column if not exists monetization_without_content boolean default false;
