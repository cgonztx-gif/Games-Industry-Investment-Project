-- Migration 003: sentiment collection controls on watchlist rows.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run
--
-- sentiment_tier controls Reddit request budget:
--   tier_a       = collect subreddit listing plus comments for top posts
--   listing_only = collect subreddit listing only
--
-- subreddit stores the explicit community target once resolved, so weekly runs do
-- not repeatedly search Reddit for every tracked game.

alter table watchlist
  add column if not exists sentiment_tier text not null default 'listing_only'
    check (sentiment_tier in ('tier_a', 'listing_only'));

alter table watchlist
  add column if not exists subreddit text;

alter table watchlist
  add column if not exists subreddit_resolved_at timestamptz;

update watchlist
set sentiment_tier = 'tier_a'
where sentiment_tier = 'listing_only'
  and (
    ticker is not null
    or exists (
      select 1
      from games g
      where g.game_id = watchlist.game_id
        and g.is_live_service = true
    )
  );

create index if not exists watchlist_sentiment_tier_idx
  on watchlist (sentiment_tier)
  where active = true;
