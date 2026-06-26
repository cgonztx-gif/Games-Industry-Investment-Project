-- Games Industry Investment Intelligence Platform
-- Supabase PostgreSQL Schema
-- Apply via: Supabase Dashboard → SQL Editor → Run

-- Enable pgvector if you want embedding-based similarity search later
-- create extension if not exists vector;

-- ============================================================
-- WATCHLIST
-- ============================================================

create table if not exists studios (
  studio_id   uuid primary key default gen_random_uuid(),
  name        text not null,
  ticker      text,                  -- parent public company ticker (e.g. TTWO, MSFT)
  parent_name text,
  created_at  timestamptz default now()
);

create table if not exists games (
  game_id        uuid primary key default gen_random_uuid(),
  title          text not null,
  studio_id      uuid references studios(studio_id),
  genre          text,
  release_date   date,
  is_live_service boolean default false,
  steam_app_id   text,
  igdb_id        text,
  rawg_slug      text,
  created_at     timestamptz default now()
);

create table if not exists watchlist (
  id          uuid primary key default gen_random_uuid(),
  game_id     uuid references games(game_id),
  studio_id   uuid references studios(studio_id),
  ticker      text,
  active      boolean default true,
  date_added  timestamptz default now(),
  added_by    text check (added_by in ('seed', 'discovery', 'manual'))
);

create table if not exists watchlist_proposals (
  proposal_id    uuid primary key default gen_random_uuid(),
  game_id        uuid references games(game_id),
  studio_id      uuid references studios(studio_id),
  trigger_signal text,               -- what caused the proposal
  claude_rationale text,             -- Claude's investment-relevance reasoning
  status         text default 'pending' check (status in ('pending', 'approved', 'rejected')),
  reviewed_at    timestamptz,
  created_at     timestamptz default now()
);

-- ============================================================
-- DATA LAYER
-- ============================================================

create table if not exists player_metrics (
  id               uuid primary key default gen_random_uuid(),
  game_id          uuid references games(game_id),
  date             date not null,
  concurrent_players integer,
  peak_players_24h   integer,
  review_score       numeric(4,2),  -- e.g. 87.5
  review_count       integer,
  review_velocity    integer,       -- new reviews in the period
  created_at         timestamptz default now(),
  unique (game_id, date)
);

create table if not exists sentiment_snapshots (
  id             uuid primary key default gen_random_uuid(),
  game_id        uuid references games(game_id),
  date           date not null,
  source         text,              -- reddit | twitter | youtube | steam
  sentiment_score numeric(3,1),    -- 1–10
  top_themes     jsonb,             -- [{"aspect": "monetization", "polarity": "negative"}, ...]
  divergence_flag boolean default false,
  vocal_minority_note text,
  created_at     timestamptz default now()
);

create table if not exists patch_events (
  id             uuid primary key default gen_random_uuid(),
  game_id        uuid references games(game_id),
  date           date not null,
  patch_type     text check (patch_type in ('hotfix', 'balance', 'content_drop', 'monetization', 'engine', 'other')),
  scope_summary  text,
  cadence_delta  integer,           -- days since last patch (negative = more frequent)
  created_at     timestamptz default now()
);

create table if not exists studio_signals (
  id           uuid primary key default gen_random_uuid(),
  studio_id    uuid references studios(studio_id),
  date         date not null,
  signal_type  text,                -- hiring_surge | layoffs | exec_departure | acquisition | ipo | press_release
  description  text,
  severity     text check (severity in ('low', 'medium', 'high')),
  source_url   text,
  created_at   timestamptz default now()
);

create table if not exists portfolio_positions_context (
  id           uuid primary key default gen_random_uuid(),
  ticker       text not null,
  studio_id    uuid references studios(studio_id),
  date         date not null,
  price        numeric(10,2),
  pe_ratio     numeric(8,2),
  earnings_date date,
  short_interest numeric(5,2),
  signal_score   numeric(3,1),      -- composite health score
  created_at   timestamptz default now(),
  unique (ticker, date)
);

-- ============================================================
-- SYNTHESIS & PORTFOLIO LAYER
-- ============================================================

create table if not exists weekly_briefings (
  id              uuid primary key default gen_random_uuid(),
  week_of         date not null unique,
  briefing_text   text,              -- full Claude output
  portfolio_update jsonb,
  top_opportunities jsonb,
  risk_flags      jsonb,
  notable_events  jsonb,
  reasoning_log   text,
  created_at      timestamptz default now()
);

create table if not exists trade_plans (
  plan_id         uuid primary key default gen_random_uuid(),
  week_of         date not null,
  briefing_id     uuid references weekly_briefings(id),
  claude_rationale text,
  status          text default 'pending' check (status in ('pending', 'approved', 'rejected')),
  reviewed_at     timestamptz,
  created_at      timestamptz default now()
);

create table if not exists trade_orders (
  order_id        uuid primary key default gen_random_uuid(),
  plan_id         uuid references trade_plans(plan_id),
  ticker          text not null,
  action          text check (action in ('buy', 'sell', 'hold')),
  size_usd        numeric(10,2),
  alpaca_order_id text,
  status          text default 'pending' check (status in ('pending', 'approved', 'rejected', 'filled', 'cancelled')),
  filled_at       timestamptz,
  created_at      timestamptz default now()
);

create table if not exists portfolio_snapshots (
  snapshot_id          uuid primary key default gen_random_uuid(),
  date                 date not null unique,
  total_value          numeric(12,2),
  cash                 numeric(12,2),
  total_return_pct     numeric(6,3),
  benchmark_return_pct numeric(6,3),  -- S&P 500
  created_at           timestamptz default now()
);

create table if not exists positions (
  position_id       uuid primary key default gen_random_uuid(),
  ticker            text not null,
  qty               numeric(12,4),
  avg_entry_price   numeric(10,2),
  current_price     numeric(10,2),
  unrealized_pnl    numeric(10,2),
  signal_source     text,
  as_of             timestamptz default now()
);
