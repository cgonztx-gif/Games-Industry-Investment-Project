"""
Sentiment Worker - Phase 2

Fetches Steam reviews, Reddit discussion, configured YouTube comments, and
this week's matched news coverage for every active watchlist game. Steam/
Reddit/YouTube are scored with VADER + Claude Haiku ABSA (community
sentiment); news is scored separately with a media stance/frame classifier
(news_stance_client.py) since it measures a different axis (see
docs/news-source-decision-memo.md §3) -- source='news' rows are NOT
VADER/ABSA-scored. Writes one row per (game_id, date, source) into
sentiment_snapshots.

Prerequisites:
  - database/migrations/001_sentiment_snapshots_unique.sql
  - database/migrations/002_api_cache.sql
  - database/migrations/003_watchlist_sentiment_targets.sql
  - database/migrations/008_news_items.sql
  - agents/workers/news/worker.py must have run earlier this week so
    get_recent_news_items() has matched articles to read.
"""

import time
from datetime import date, timedelta

from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_last_player_metrics,
    get_recent_news_items,
    get_watchlist_games,
    update_watchlist_subreddit,
    write_sentiment_snapshot,
)
from agents.workers.sentiment.absa_client import run_absa
from agents.workers.sentiment.divergence import compute_divergence
from agents.workers.sentiment.news_stance_client import classify_stance
from agents.workers.sentiment.reddit_cache import SupabaseRedditCache
from agents.workers.sentiment.reddit_source import (
    RedditBlocked,
    SubredditResolver,
    build_reddit_source,
    build_subreddit_resolver,
    cached_resolve_subreddit,
)
from agents.workers.sentiment.steam_reviews_client import fetch_steam_reviews
from agents.workers.sentiment.vader_scorer import score_texts
from agents.workers.sentiment.youtube_client import (
    fetch_youtube_comments,
    load_game_youtube_playlists,
)

_NEWS_LOOKBACK_DAYS = 7

_MIN_TEXTS_FOR_ABSA = 5

# How many of a tier_a game's top posts also get their comments fetched. Each
# comment fetch is a separate Reddit request (billed through the ScrapeOps
# residential proxy), and comments were ~87% of this pipeline's recurring
# Reddit proxy spend -- so this is the dominant ScrapeOps cost lever. Set to 0
# (2026-07-22) to DROP comment fetching entirely for credit control: sentiment
# is now scored from post titles/selftext only (1 request per game, vs ~11).
# Bump back to a small number (e.g. 3-5) to re-enable a cheaper slice of
# comment sentiment; `posts[:0]` is an empty slice, so 0 fetches no comments.
_TIER_A_COMMENT_POSTS = 0

# Wall-clock budget for the per-game loop. Each game runs LLM calls (Haiku
# ABSA on community text, Haiku/Sonnet news-stance), so on a cold cache the
# full ~4,000-game watchlist can run for hours -- CI run 29607547902's
# sentiment phase ran to GitHub's `timeout-minutes: 350` cap and was
# hard-killed (SIGTERM mid-write, `failure` conclusion, no clean summary).
# Unlike the news worker's three passes that share one job, sentiment is the
# SOLE pass in its own 350-min-capped job, so it gets a larger budget: 300 min
# leaves ~50 min of margin under the cap for a clean abort-and-degrade (break
# the loop, write the summary, return `success` with partial coverage) instead
# of the dirty kill. Games are processed tier_a-first (see run()) so if the
# budget does fire, the high-value games are the ones that got covered.
_PASS_TIME_BUDGET_SECONDS = 18000  # 300 min


def _write_source_snapshot(
    db,
    *,
    game_id: str,
    title: str,
    today: str,
    source: str,
    texts_with_weights: list[dict],
    player_metrics: dict | None,
) -> bool:
    if not texts_with_weights:
        return False

    score = score_texts(texts_with_weights)
    texts = [item["text"] for item in texts_with_weights if item.get("text")]
    themes = (
        run_absa(title, source, texts)
        if len(texts_with_weights) >= _MIN_TEXTS_FOR_ABSA
        else []
    )
    flag, note = compute_divergence(score, player_metrics)
    write_sentiment_snapshot(
        db,
        {
            "game_id": game_id,
            "date": today,
            "source": source,
            "sentiment_score": score,
            "top_themes": themes,
            "divergence_flag": flag,
            "vocal_minority_note": note,
        },
    )
    return True


def _write_news_snapshot(
    db,
    *,
    game_id: str,
    title: str,
    today: str,
    news_items: list[dict],
    sentiment_tier: str,
    player_metrics: dict | None,
) -> bool:
    """
    News gets its own aggregate stance/frame classification, not the VADER +
    ABSA + vocal-minority pipeline _write_source_snapshot runs for community
    sources -- see news_stance_client.py's docstring. classify_stance()
    returning None (any failure) means skip writing this row entirely, since
    that call *is* the score, not an enrichment on top of one.
    """
    if not news_items:
        return False

    stance = classify_stance(title, news_items, sentiment_tier=sentiment_tier)
    if stance is None:
        return False

    flag, note = compute_divergence(stance["score"], player_metrics)
    write_sentiment_snapshot(
        db,
        {
            "game_id": game_id,
            "date": today,
            "source": "news",
            "sentiment_score": stance["score"],
            "top_themes": stance["themes"],
            "divergence_flag": flag,
            "vocal_minority_note": f"{stance['outlet_count']} outlets — {stance['coverage_note']}",
        },
    )
    return True


def _resolve_subreddit_for_game(db, game: dict, resolver: SubredditResolver, lookup_cache) -> str | None:
    stored_subreddit = game.get("subreddit")
    if stored_subreddit is not None:
        return stored_subreddit or None

    subreddit = cached_resolve_subreddit(
        game.get("title", ""),
        resolver,
        lookup_cache,
    )
    watchlist_id = game.get("watchlist_id")
    # Only PERSIST a real resolution to watchlist.subreddit. A no-match must
    # NOT be written, because watchlist.subreddit has no TTL: once a game is
    # stored as '' (no-match), the early-return above skips it forever, so it
    # never re-resolves even after conditions change. That is exactly how
    # ~4,014 games got stuck with subreddit='' during the 2026-07-09..07-13
    # Reddit-blocked/soft-failure window and stopped producing Reddit sentiment
    # entirely (only 3 games had real subreddits by 2026-07-21). No-matches are
    # already cached with a 30-day TTL by cached_resolve_subreddit's api_cache
    # layer, which throttles re-queries without poisoning the watchlist
    # permanently -- so a no-match now stays NULL here and self-heals on a
    # later run once that cache entry expires.
    if watchlist_id and subreddit:
        update_watchlist_subreddit(db, watchlist_id, subreddit)
    return subreddit


def _reddit_texts_for_game(reddit_source, subreddit: str, sentiment_tier: str) -> list[dict]:
    posts = reddit_source.fetch_posts(subreddit, sort="top", timeframe="week", limit=50)
    texts: list[dict] = [
        {
            "text": f"{post.title} {post.selftext[:500]}".strip(),
            "score": post.score,
        }
        for post in posts
    ]

    if sentiment_tier != "tier_a":
        return texts

    for post in posts[:_TIER_A_COMMENT_POSTS]:
        comments = reddit_source.fetch_comments(post.id, subreddit, limit=100)
        for comment in comments:
            body = comment.body.strip()
            if not body or body in {"[deleted]", "[removed]"}:
                continue
            texts.append({"text": body[:600], "score": comment.score})
    return texts


def run(now_fn=time.monotonic) -> dict:
    db = get_client()
    today_date = date.today()
    today = today_date.isoformat()
    news_since = (today_date - timedelta(days=_NEWS_LOOKBACK_DAYS)).isoformat()
    games = get_watchlist_games(db)

    # Process tier_a (high-value) games first, so if the wall-clock budget
    # below fires mid-watchlist the games that got covered are the important
    # ones, not whatever happened to be last in watchlist order. Stable sort
    # keeps the existing order within each tier.
    games = sorted(games, key=lambda g: 0 if (g.get("sentiment_tier") or "listing_only") == "tier_a" else 1)

    lookup_cache = SupabaseRedditCache(client=db, source="subreddit_lookup")
    steam_cache = SupabaseApiCache(client=db, source="steam_review_text")
    youtube_cache = SupabaseApiCache(client=db, source="youtube_comments")
    game_youtube_playlists = load_game_youtube_playlists()
    subreddit_resolver = build_subreddit_resolver()
    reddit_source = build_reddit_source(lambda source: SupabaseRedditCache(client=db, source=source))

    processed = []
    errors = []
    skipped_no_data = 0
    reddit_blocked_count = 0
    first_reddit_block_reason: str | None = None
    budget_exhausted = False

    pass_start = now_fn()
    for i, game in enumerate(games):
        if now_fn() - pass_start > _PASS_TIME_BUDGET_SECONDS:
            budget_exhausted = True
            skipped = len(games) - i
            print(
                f"[sentiment] time budget ({_PASS_TIME_BUDGET_SECONDS}s) exceeded, "
                f"aborting remaining games ({i}/{len(games)} attempted, {skipped} skipped)"
            )
            break
        if i % 10 == 0:
            print(f"[sentiment] Progress: {i}/{len(games)}")

        game_id = game["game_id"]
        title = game.get("title", "unknown")
        steam_app_id = game.get("steam_app_id")
        sentiment_tier = game.get("sentiment_tier") or "listing_only"
        wrote_any = False

        try:
            player_metrics = get_last_player_metrics(db, game_id)

            if steam_app_id:
                reviews = fetch_steam_reviews(
                    steam_app_id,
                    num_per_page=50,
                    cache=steam_cache,
                )
                wrote_any = _write_source_snapshot(
                    db,
                    game_id=game_id,
                    title=title,
                    today=today,
                    source="steam",
                    texts_with_weights=reviews,
                    player_metrics=player_metrics,
                ) or wrote_any

            # Reddit is the only ScrapeOps-billed source, so only pay for it on
            # equity-mapped games -- ones whose watchlist row has a tracked
            # ticker. Sentiment that can't feed a financial signal isn't worth
            # the residential-proxy cost, and this caps the Reddit-active set to
            # the (small) tracked-equity universe instead of growing with the
            # full watchlist as subreddits get backfilled. Non-equity games skip
            # both subreddit resolution and post-fetching. A read-time gate only:
            # nothing is persisted, so a game becomes eligible automatically if
            # it later gains a ticker (no risk of a sticky-sentinel freeze).
            try:
                if game.get("ticker"):
                    subreddit = _resolve_subreddit_for_game(
                        db,
                        game,
                        subreddit_resolver,
                        lookup_cache,
                    )
                    if subreddit:
                        reddit_texts = _reddit_texts_for_game(
                            reddit_source,
                            subreddit,
                            sentiment_tier,
                        )
                        wrote_any = _write_source_snapshot(
                            db,
                            game_id=game_id,
                            title=title,
                            today=today,
                            source="reddit",
                            texts_with_weights=reddit_texts,
                            player_metrics=player_metrics,
                        ) or wrote_any
            except RedditBlocked as exc:
                reddit_blocked_count += 1
                if first_reddit_block_reason is None:
                    first_reddit_block_reason = str(exc)
                    print(f"[sentiment] Reddit blocked (first occurrence): {exc}")

            youtube_comments = fetch_youtube_comments(
                title,
                cache=youtube_cache,
                game_playlist_ids=game_youtube_playlists.get(title),
            )
            wrote_any = _write_source_snapshot(
                db,
                game_id=game_id,
                title=title,
                today=today,
                source="youtube",
                texts_with_weights=youtube_comments,
                player_metrics=player_metrics,
            ) or wrote_any

            news_items = get_recent_news_items(db, game_id, since=news_since)
            wrote_any = _write_news_snapshot(
                db,
                game_id=game_id,
                title=title,
                today=today,
                news_items=news_items,
                sentiment_tier=sentiment_tier,
                player_metrics=player_metrics,
            ) or wrote_any

            if not wrote_any:
                skipped_no_data += 1
            else:
                processed.append({"game_id": game_id, "title": title})

        except Exception as exc:
            errors.append({"title": title, "error": str(exc)})

    if reddit_blocked_count:
        print(
            f"[sentiment] Reddit blocked for {reddit_blocked_count}/{len(games)} games "
            f"this run (reason: {first_reddit_block_reason}); Reddit sentiment coverage "
            "degraded for this run — other sources (Steam, YouTube) are unaffected."
        )

    budget_note = " (time budget exhausted — partial coverage)" if budget_exhausted else ""
    print(
        f"[sentiment] Done - {len(processed)} games written, "
        f"{skipped_no_data} skipped (no data), {len(errors)} errors{budget_note}."
    )
    return {
        "date": today,
        "games_processed": len(processed),
        "skipped_no_data": skipped_no_data,
        "error_count": len(errors),
        "errors": errors,
        "reddit_blocked_count": reddit_blocked_count,
        "budget_exhausted": budget_exhausted,
    }
