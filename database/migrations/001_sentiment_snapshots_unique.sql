-- Migration 001: unique constraint on sentiment_snapshots
-- Required for upsert idempotency in the sentiment worker.
-- Apply via: Supabase Dashboard → SQL Editor → Run

ALTER TABLE sentiment_snapshots
  ADD CONSTRAINT uq_sentiment_game_date_source
  UNIQUE (game_id, date, source);
