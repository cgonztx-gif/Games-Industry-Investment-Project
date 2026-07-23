-- Migration 017: games.canonical_game_id — de-duplicate edition/season/DLC
-- variant rows down to one canonical base-game row.
--
-- The seeded/discovered games set (~4,018 rows) ingested every Steam store SKU
-- as a distinct games row: base game + editions ("Deluxe"/"Collector's"/
-- "Launch"/"Limited") + season passes + cosmetic/DLC packs. The dashboard's
-- Game Signals grid shows the failure mode: six separate "Destiny 2" rows
-- (Digital Deluxe, Launch, Collector's, ...) all reporting the identical
-- 45,601 current CCU, because they all share one steam_app_id (1085660) and
-- therefore one player-count source. This inflates every per-game worker cost
-- (Steam / sentiment / ScrapeOps / LLM passes), skews coverage-percentage
-- math, and clutters the dashboard.
--
-- This column records, for each non-canonical variant row, the game_id of the
-- canonical base-game row it collapses into. NULL means "this row is itself
-- canonical (or not yet processed)". It makes the dedup:
--   * auditable  — the mapping is queryable, not hidden in a one-off script
--   * reversible — deactivation is via watchlist.active=false; clearing this
--                  column + reactivating restores the prior state
--   * reusable   — the dashboard can later collapse editions to their base by
--                  grouping on coalesce(canonical_game_id, game_id).
--
-- The actual row deactivation (watchlist.active=false for variants) and the
-- population of this column are performed by scripts/dedup_watchlist.py
-- (dry-run-first, mirrors scripts/rawg_backfill.py). This migration only adds
-- the column + a lookup index; it changes no data on its own.
--
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

alter table games
  add column if not exists canonical_game_id uuid references games(game_id);

create index if not exists games_canonical_game_id_idx
  on games (canonical_game_id);
