-- Migration 007: widen player_metrics.review_score to allow a perfect 100.00.
-- numeric(4,2) tops out at 99.99, but steam_client.py's positive/total*100 can
-- legitimately produce 100.00 for a perfectly-reviewed game, which fails the upsert.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

alter table player_metrics
  alter column review_score type numeric(5,2);
