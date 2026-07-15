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

from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_watchlist_entities_with_aliases,
    write_news_item,
)
from agents.workers.news.entity_matcher import resolve_matched_entities
from agents.workers.news.gdelt_client import CachedGdeltSource, GdeltBlocked, GdeltSource
from agents.workers.news.google_news_client import CachedGoogleNewsSource, GoogleNewsSource
from agents.workers.news.rss_client import CachedRssSource, fetch_curated_feeds

# Consecutive-failure circuit breaker for the per-entity GDELT/Google News
# passes below. Both loops query one external API per watchlist entity
# (~4,017 today); tiering the entity set was considered first (mirroring the
# sentiment worker's tier_a/listing_only split) but rejected -- live data
# showed 3,977/4,017 active watchlist rows are already tier_a, so it wouldn't
# meaningfully shrink either loop. A sustained outage/throttling window (the
# 2026-07-13 incident: ~5 hours of GDELT 429s/timeouts, run cancelled before
# finishing) instead needs a hard stop: abort the remaining pass once this
# many *consecutive* calls fail, rather than paying full per-entity retry
# cost across the whole watchlist. Isolated failures (a handful of
# entities with no real coverage) don't trip it.
_CONSECUTIVE_FAILURE_LIMIT = 10


def _dedupe_by_url(articles: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for article in articles:
        url = article.get("url")
        if url and url not in seen:
            seen[url] = article
    return list(seen.values())


def _gdelt_query_for_entity(entity: dict) -> str:
    return f'"{entity["title"]}"'


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


def run() -> dict:
    db = get_client()
    entities = get_watchlist_entities_with_aliases(db)

    rss_source = CachedRssSource(cache=SupabaseApiCache(client=db, source="news_rss"))
    gdelt_source = CachedGdeltSource(GdeltSource(), cache=SupabaseApiCache(client=db, source="gdelt"))
    gnews_source = CachedGoogleNewsSource(
        GoogleNewsSource(), cache=SupabaseApiCache(client=db, source="gnews_rss")
    )
    disambig_cache = SupabaseApiCache(client=db, source="news_disambiguation")

    articles: list[dict] = list(fetch_curated_feeds(rss_source))

    consecutive_gdelt_failures = 0
    for i, entity in enumerate(entities):
        try:
            articles.extend(gdelt_source.search(_gdelt_query_for_entity(entity)))
            consecutive_gdelt_failures = 0
        except GdeltBlocked as exc:
            print(f"[news] GDELT blocked for {entity['title']!r}: {exc}")
            consecutive_gdelt_failures += 1
            if consecutive_gdelt_failures >= _CONSECUTIVE_FAILURE_LIMIT:
                skipped = len(entities) - i - 1
                print(
                    f"[news] GDELT: {consecutive_gdelt_failures} consecutive failures, "
                    f"aborting remaining GDELT pass ({i + 1}/{len(entities)} attempted, "
                    f"{skipped} skipped)"
                )
                break

    unique_articles = _dedupe_by_url(articles)

    items_written = 0
    coverage_counts: dict[str, int] = {}
    for article in unique_articles:
        matched = resolve_matched_entities(article, entities, disambig_cache)
        if not matched:
            continue
        _write_matched_article(db, article, matched)
        items_written += 1
        for game_id in matched:
            coverage_counts[game_id] = coverage_counts.get(game_id, 0) + 1

    # Backfill entities GDELT + curated RSS didn't surface anything for. When
    # the GDELT pass above circuit-breaks early, most entities never got a
    # GDELT query at all and will show zero coverage here too -- so this loop
    # needs the same consecutive-failure circuit breaker, or an outage just
    # relocates from GDELT to Google News instead of being bounded.
    thin_entities = [e for e in entities if coverage_counts.get(e["game_id"], 0) < 1]
    consecutive_gnews_failures = 0
    for i, entity in enumerate(thin_entities):
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
            matched = resolve_matched_entities(article, [entity], disambig_cache)
            if not matched:
                continue
            _write_matched_article(db, article, matched)
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
