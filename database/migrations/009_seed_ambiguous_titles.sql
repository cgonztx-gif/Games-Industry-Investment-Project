-- Migration 009: seed games.title_is_ambiguous = true for titles that are
-- themselves common English words/phrases, so the news entity matcher
-- (agents/workers/news/entity_matcher.py) forces Stage-2 Haiku disambiguation
-- on them instead of trusting a bare Stage-1 word-boundary match. Migration
-- 008 added the column defaulted to false for every row; nothing was seeded.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

update games
set title_is_ambiguous = true
where title in (
  'Anthem', 'Arms', 'Ball', 'Bound', 'Concord', 'Doom', 'Dreams', 'Erica',
  'Golf', 'Grounded', 'Humankind', 'Judgment', 'Keeper', 'Kiln', 'Kitchen',
  'Maiden', 'Marathon', 'Overwatch', 'Prey', 'Quake', 'Rad', 'Rise', 'Rust',
  'SCUM', 'Siren', 'Spatter', 'Squad', 'Stifled', 'Uno', 'Vermin'
);
