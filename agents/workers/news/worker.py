"""
News ingestion worker (news-article stream, see docs/news-source-decision-memo.md).

Fetches games-industry news from curated RSS feeds + per-entity GDELT
queries (+ Google News RSS as a per-entity fallback for thin coverage),
resolves which watchlist game(s) each article is about, and writes matched
articles into news_items. This worker owns fetch + cache + relevance-matching
only -- it does not score sentiment. The Sentiment worker
(agents/workers/sentiment/worker.py) reads news_items and classifies
stance/frame per game per week.

Prerequisites:
  - database/migrations/002_api_cache.sql
  - database/migrations/008_news_items.sql
"""

import time

from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_watchlist_entities_with_aliases,
    write_news_item,
)
from agents.workers.news.entity_matcher import resolve_matched_entities
from agents.workers.news.gdelt_client import (
    INDUSTRY_GDELT_QUERIES,
    CachedGdeltSource,
    GdeltBlocked,
    GdeltSource,
)
from agents.workers.news.google_news_client import CachedGoogleNewsSource, GoogleNewsSource
from agents.workers.news.rss_client import CachedRssSource, fetch_curated_feeds

# Consecutive-failure circuit breaker for the GDELT and Google News passes
# below. The GDELT pass is now a small FIXED set of industry-level queries
# (INDUSTRY_GDELT_QUERIES, ~10) matched to entities locally -- NOT one query
# per watchlist entity, which was structurally infeasible at ~4,017 queries
# (the 2026-07-13..07-17 incident chain; see tasks.md and gdelt_client.py's
# INDUSTRY_GDELT_QUERIES note). The Google News fallback loop is still
# per-entity (one query per thin-coverage entity), so the breaker/budget still
# matter most there; on the ~10-query GDELT loop they're near-vestigial but
# kept as cheap defense-in-depth and to preserve the abort-log style.
#
# A sustained outage/throttling window needs a hard stop: abort the remaining
# pass once this many *consecutive* calls fail, rather than grinding through
# the rest at full per-call retry cost. Isolated failures don't trip it.
#
# The limit is enforced at two layers. The loop-level breakers below only see
# failures that propagate out of the cached sources -- a stale-cache rescue
# looks like a success here and resets the loop counter, even though the live
# call failed and paid its full retry cost first (confirmed live in CI run
# 29438324984, 2026-07-15: 317 exhausted-retry cycles interleaved with 196
# stale serves, ~6h, loop breaker never tripped). The source-level breakers
# inside CachedGdeltSource/CachedGoogleNewsSource count consecutive
# *live-call* failures before any stale rescue and stop calling the network
# entirely once tripped; the loop breakers remain as a secondary guard for
# the no-cache case. Both are streak-based, so neither bounds a slow bleed
# where successes interleave with expensive failures -- that is what the
# _PASS_TIME_BUDGET_SECONDS wall-clock budget below is for.
_CONSECUTIVE_FAILURE_LIMIT = 10

# Wall-clock budget per pass (the GDELT query-set loop, the per-entity Google
# News fallback loop) -- the authoritative bound on how long either pass may
# run. CI run 29516590262 (2026-07-16) proved the consecutive-failure breakers
# above do not bound the OLD per-entity GDELT pass under GDELT's real failure
# mode: the job died at GitHub's 6-hour cap with zero breaker trips -- 1,037
# throttled 429s and 395 entities exhausting retries, but successes
# interleaved often enough that the consecutive-live-failure streak kept
# resetting. That slow bleed (~45s per failed entity) is what the industry-
# query redesign eliminates for GDELT; the budget stays as the authoritative
# bound for the still-per-entity Google News fallback (and as cheap insurance
# on the small GDELT loop). The breakers stay as the fast path for genuine
# total outages (they trip in ~minutes; the budget takes 90).
_PASS_TIME_BUDGET_SECONDS = 5400  # 90 min


def _dedupe_by_url(articles: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for article in articles:
        url = article.get("url")
        if url and url not in seen:
            seen[url] = article
    return list(seen.values())


def _write_matched_article(db, article: dict, matched: list[str]) -> None:
    write_news_item(
        db,
        {
            "url": article["url"],
            "title": article.get("title") or "",
            "snippet": article.get("snippet") or "",
            "published_at": article.get("published_at"),
            "domain": article.get("domain") or "",
            "matched_entities": matched,
        },
    )


def _match_and_write(db, article: dict, entities: list[dict], disambig_cache) -> list[str]:
    """Match one article against `entities` and write it if matched. Per-item
    degrade: logs and returns [] on ANY error (a matcher exception or a DB
    write failure) rather than aborting the whole news pass -- matching the
    degrade-per-item convention every other worker's run() loop uses. This
    matters more since the industry-query GDELT redesign pulls a much larger,
    more varied article corpus (a single malformed article must not sink the
    phase after all the fetching work)."""
    try:
        matched = resolve_matched_entities(article, entities, disambig_cache)
        if not matched:
            return []
        _write_matched_article(db, article, matched)
        return matched
    except Exception as exc:  # noqa: BLE001 -- deliberate per-item isolation
        print(f"[news] match/write failed for {article.get('url')!r}: {exc}")
        return []


def run(now_fn=time.monotonic) -> dict:
    db = get_client()
    entities = get_watchlist_entities_with_aliases(db)

    rss_source = CachedRssSource(cache=SupabaseApiCache(client=db, source="news_rss"))
    gdelt_source = CachedGdeltSource(
        GdeltSource(),
        cache=SupabaseApiCache(client=db, source="gdelt"),
        max_consecutive_live_failures=_CONSECUTIVE_FAILURE_LIMIT,
    )
    gnews_source = CachedGoogleNewsSource(
        GoogleNewsSource(),
        cache=SupabaseApiCache(client=db, source="gnews_rss"),
        max_consecutive_live_failures=_CONSECUTIVE_FAILURE_LIMIT,
    )
    disambig_cache = SupabaseApiCache(client=db, source="news_disambiguation")

    articles: list[dict] = list(fetch_curated_feeds(rss_source))

    # Industry-level GDELT pass: a small fixed set of broad games-industry
    # queries (INDUSTRY_GDELT_QUERIES), bound to specific watchlist entities
    # locally by resolve_matched_entities below -- NOT one query per entity.
    consecutive_gdelt_failures = 0
    gdelt_pass_start = now_fn()
    for i, query in enumerate(INDUSTRY_GDELT_QUERIES):
        if now_fn() - gdelt_pass_start > _PASS_TIME_BUDGET_SECONDS:
            skipped = len(INDUSTRY_GDELT_QUERIES) - i
            print(
                f"[news] GDELT: time budget ({_PASS_TIME_BUDGET_SECONDS}s) exceeded, "
                f"aborting remaining GDELT pass ({i}/{len(INDUSTRY_GDELT_QUERIES)} attempted, "
                f"{skipped} skipped)"
            )
            break
        try:
            articles.extend(gdelt_source.search(query))
            consecutive_gdelt_failures = 0
        except GdeltBlocked as exc:
            print(f"[news] GDELT blocked for query {query!r}: {exc}")
            consecutive_gdelt_failures += 1
            if consecutive_gdelt_failures >= _CONSECUTIVE_FAILURE_LIMIT:
                skipped = len(INDUSTRY_GDELT_QUERIES) - i - 1
                print(
                    f"[news] GDELT: {consecutive_gdelt_failures} consecutive failures, "
                    f"aborting remaining GDELT pass ({i + 1}/{len(INDUSTRY_GDELT_QUERIES)} attempted, "
                    f"{skipped} skipped)"
                )
                break

    unique_articles = _dedupe_by_url(articles)

    # The entity-matching pass runs a cached Haiku disambiguation call per
    # ambiguous (article, entity) pair, so on a cold `news_disambiguation`
    # cache it is LLM-bound and slow over the industry-query redesign's larger
    # ~2,300-article corpus (~144 min in CI run 29607547902). Give it the same
    # wall-clock budget the GDELT/Google-News passes have, measured from its
    # own start, so it aborts-and-degrades cleanly instead of pushing the news
    # job toward the 6-hour/timeout-minutes cap. Aborting early just leaves
    # coverage_counts partial -> more entities look "thin" -> the (also
    # budgeted) Google-News fallback below picks them up, and the warmed cache
    # makes the next run fast.
    items_written = 0
    coverage_counts: dict[str, int] = {}
    match_pass_start = now_fn()
    for i, article in enumerate(unique_articles):
        if now_fn() - match_pass_start > _PASS_TIME_BUDGET_SECONDS:
            skipped = len(unique_articles) - i
            print(
                f"[news] matching: time budget ({_PASS_TIME_BUDGET_SECONDS}s) exceeded, "
                f"aborting remaining matching pass ({i}/{len(unique_articles)} attempted, "
                f"{skipped} skipped)"
            )
            break
        matched = _match_and_write(db, article, entities, disambig_cache)
        if not matched:
            continue
        items_written += 1
        for game_id in matched:
            coverage_counts[game_id] = coverage_counts.get(game_id, 0) + 1

    # Backfill entities the industry-level GDELT pass + curated RSS didn't
    # surface anything for -- the long tail the fixed query set can't reach
    # under the 250-record-per-query cap. This loop is still per-entity, so it
    # keeps its own consecutive-failure breaker + time budget (a Google News
    # outage across the whole thin set must stay bounded).
    thin_entities = [e for e in entities if coverage_counts.get(e["game_id"], 0) < 1]
    consecutive_gnews_failures = 0
    gnews_pass_start = now_fn()
    for i, entity in enumerate(thin_entities):
        if now_fn() - gnews_pass_start > _PASS_TIME_BUDGET_SECONDS:
            skipped = len(thin_entities) - i
            print(
                f"[news] Google News: time budget ({_PASS_TIME_BUDGET_SECONDS}s) exceeded, "
                f"aborting remaining fallback pass ({i}/{len(thin_entities)} attempted, "
                f"{skipped} skipped)"
            )
            break
        try:
            extra_articles = gnews_source.search(entity["title"])
            consecutive_gnews_failures = 0
        except Exception as exc:
            print(f"[news] Google News fallback failed for {entity['title']!r}: {exc}")
            consecutive_gnews_failures += 1
            if consecutive_gnews_failures >= _CONSECUTIVE_FAILURE_LIMIT:
                skipped = len(thin_entities) - i - 1
                print(
                    f"[news] Google News: {consecutive_gnews_failures} consecutive failures, "
                    f"aborting remaining fallback pass ({i + 1}/{len(thin_entities)} attempted, "
                    f"{skipped} skipped)"
                )
                break
            continue
        for article in _dedupe_by_url(extra_articles):
            matched = _match_and_write(db, article, [entity], disambig_cache)
            if not matched:
                continue
            items_written += 1
            coverage_counts[entity["game_id"]] = coverage_counts.get(entity["game_id"], 0) + 1

    entities_with_coverage = sum(1 for count in coverage_counts.values() if count > 0)
    print(
        f"[news] Done - {len(unique_articles)} articles fetched, {items_written} matched & written, "
        f"{entities_with_coverage}/{len(entities)} entities with coverage."
    )
    return {
        "articles_fetched": len(unique_articles),
        "items_written": items_written,
        "entities_with_coverage": entities_with_coverage,
    }
