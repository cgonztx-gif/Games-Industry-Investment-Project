-- Migration 005: rename analytical equity context to equity_signals.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

create table if not exists equity_signals (
  id             uuid primary key default gen_random_uuid(),
  ticker         text not null,
  studio_id      uuid references studios(studio_id),
  date           date not null,
  current_price  numeric(10,2),
  pe_ratio       numeric(8,2),
  earnings_date  date,
  short_interest numeric(5,2),
  health_score   numeric(3,1),
  current_signal text,
  recommendation text,
  created_at     timestamptz default now(),
  unique (ticker, date)
);

insert into equity_signals (
  ticker,
  studio_id,
  date,
  current_price,
  pe_ratio,
  earnings_date,
  short_interest,
  health_score,
  created_at
)
select
  ticker,
  studio_id,
  date,
  price,
  pe_ratio,
  earnings_date,
  short_interest,
  signal_score,
  created_at
from portfolio_positions_context
on conflict (ticker, date) do nothing;
