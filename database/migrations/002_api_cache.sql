-- Generic JSON blob cache for volatile external sources (Reddit, Steam, YouTube, etc.)
-- Apply in Supabase SQL Editor before the first run of the unauthenticated Reddit adapter.
-- Referenced by: agents/workers/sentiment/reddit_cache.py (SupabaseRedditCache)

create table if not exists api_cache (
    source      text        not null,
    key         text        not null,
    payload     jsonb       not null,
    fetched_at  timestamptz not null default now(),
    primary key (source, key)
);

create index if not exists api_cache_fetched_at_idx on api_cache (fetched_at);

-- Weekly cleanup (run via Supabase pg_cron or as a step in GitHub Actions):
-- delete from api_cache where fetched_at < now() - interval '14 days';
