-- Migration 018: watchlist.deactivated_reason — record WHY a row was
-- soft-deactivated, so the growing set of active=false rows stays auditable and
-- each cleanup pass is individually reversible.
--
-- The bloat-reduction work (tasks.md "Reduce watchlist/game-list bloat")
-- soft-deactivates watchlist rows (active=false) in several passes:
--   * edition/season/DLC dedup (scripts/dedup_watchlist.py) — recorded via
--     games.canonical_game_id (migration 017)
--   * dead / no-coverage rows (scripts/deactivate_dead_games.py) — has no
--     canonical mapping, so without this column an active=false dead row is
--     indistinguishable from a manual or future-pass deactivation.
--
-- This free-text column tags the originating pass ('edition_dedup',
-- 'dead_no_coverage', ...). NULL = still active, or deactivated before this
-- column existed. Reversing a pass is then a scoped
-- `update watchlist set active=true where deactivated_reason='<pass>'`.
--
-- The backfill below retro-tags the edition-dedup rows already deactivated by
-- migration 017 + dedup_watchlist.py, so the audit trail is complete rather
-- than starting only from the dead-row pass.
--
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

alter table watchlist
  add column if not exists deactivated_reason text;

update watchlist w
   set deactivated_reason = 'edition_dedup'
  from games g
 where w.game_id = g.game_id
   and g.canonical_game_id is not null
   and w.active = false
   and w.deactivated_reason is null;
