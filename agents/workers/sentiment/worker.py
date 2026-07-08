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
from agents.workers.sentiment.youtube_client import fetch_youtube_comments

_NEWS_LOOKBACK_DAYS = 7

_MIN_TEXTS_FOR_ABSA = 5
_TIER_A_COMMENT_POSTS = 10


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
    if watchlist_id:
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


def run() -> dict:
    db = get_client()
    today_date = date.today()
    today = today_date.isoformat()
    news_since = (today_date - timedelta(days=_NEWS_LOOKBACK_DAYS)).isoformat()
    games = get_watchlist_games(db)

    lookup_cache = SupabaseRedditCache(client=db, source="subreddit_lookup")
    steam_cache = SupabaseApiCache(client=db, source="steam_review_text")
    youtube_cache = SupabaseApiCache(client=db, source="youtube_comments")
    subreddit_resolver = build_subreddit_resolver()
    reddit_source = build_reddit_source(lambda source: SupabaseRedditCache(client=db, source=source))

    processed = []
    errors = []
    skipped_no_data = 0
    reddit_blocked_count = 0
    first_reddit_block_reason: str | None = None

    for i, game in enumerate(games):
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

            try:
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

            youtube_comments = fetch_youtube_comments(title, cache=youtube_cache)
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

    print(
        f"[sentiment] Done - {len(processed)} games written, "
        f"{skipped_no_data} skipped (no data), {len(errors)} errors."
    )
    return {
        "date": today,
        "games_processed": len(processed),
        "skipped_no_data": skipped_no_data,
        "error_count": len(errors),
        "errors": errors,
        "reddit_blocked_count": reddit_blocked_count,
    }
