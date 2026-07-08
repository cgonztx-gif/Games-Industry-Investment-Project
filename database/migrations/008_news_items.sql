-- Migration 008: add news_items table and per-game alias/ambiguity columns
-- for the news-article-stream source (games-press + GDELT coverage feeding
-- the Sentiment worker's new source='news' rows). See
-- docs/news-source-decision-memo.md for the design rationale.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create table if not exists news_items (
  url               text primary key,
  published_at      timestamptz,
  domain            text,
  title             text not null,
  snippet           text,
  matched_entities  jsonb not null default '[]',  -- array of games.game_id strings
  fetched_at        timestamptz not null default now()
);
create index if not exists news_items_published_at_idx on news_items (published_at);

alter table games add column if not exists aliases text[] not null default '{}';
alter table games add column if not exists title_is_ambiguous boolean not null default false;
