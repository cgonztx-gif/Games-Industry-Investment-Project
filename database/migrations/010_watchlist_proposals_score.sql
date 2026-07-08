-- Migration 010: add score + recommended_sentiment_tier to watchlist_proposals
-- for the Discovery agent (agents/workers/discovery/worker.py). Both fields are
-- named in agents/skills/watchlist-relevance-scoring/SKILL.md's discovery-proposal
-- output contract but had no backing column yet.
-- Apply via: Supabase Dashboard -> SQL Editor -> Run

alter table watchlist_proposals
  add column if not exists score integer check (score >= 0 and score <= 100),
  add column if not exists recommended_sentiment_tier text
    check (recommended_sentiment_tier in ('tier_a', 'listing_only'));
