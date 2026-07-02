"""
Sentiment Worker - Phase 2

Fetches Steam reviews, Reddit discussion, and configured YouTube comments for
every active watchlist game, scores them with VADER + Claude Haiku ABSA, and
writes one row per (game_id, date, source) into sentiment_snapshots.

Prerequisites:
  - database/migrations/001_sentiment_snapshots_unique.sql
  - database/migrations/002_api_cache.sql
  - database/migrations/003_watchlist_sentiment_targets.sql
"""

from datetime import date

from database.api_cache import SupabaseApiCache
from database.db_client import (
    get_client,
    get_last_player_metrics,
    get_watchlist_games,
    update_watchlist_subreddit,
    write_sentiment_snapshot,
)
from agents.workers.sentiment.absa_client import run_absa
from agents.workers.sentiment.divergence import compute_divergence
from agents.workers.sentiment.reddit_cache import SupabaseRedditCache
from agents.workers.sentiment.reddit_source import (
    JsonRedditSource,
    RedditBlocked,
    build_reddit_source,
    cached_resolve_subreddit,
)
from agents.workers.sentiment.steam_reviews_client import fetch_steam_reviews
from agents.workers.sentiment.vader_scorer import score_texts
from agents.workers.sentiment.youtube_client import fetch_youtube_comments

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


def _resolve_subreddit_for_game(db, game: dict, json_source: JsonRedditSource, lookup_cache) -> str | None:
    stored_subreddit = game.get("subreddit")
    if stored_subreddit is not None:
        return stored_subreddit or None

    subreddit = cached_resolve_subreddit(
        game.get("title", ""),
        json_source,
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
    today = date.today().isoformat()
    games = get_watchlist_games(db)

    json_source = JsonRedditSource()
    lookup_cache = SupabaseRedditCache(client=db, source="subreddit_lookup")
    post_cache = SupabaseRedditCache(client=db, source="reddit")
    steam_cache = SupabaseApiCache(client=db, source="steam_review_text")
    youtube_cache = SupabaseApiCache(client=db, source="youtube_comments")
    reddit_source = build_reddit_source(post_cache)

    processed = []
    errors = []
    skipped_no_data = 0

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
                    json_source,
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
            except RedditBlocked:
                pass

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

            if not wrote_any:
                skipped_no_data += 1
            else:
                processed.append({"game_id": game_id, "title": title})

        except Exception as exc:
            errors.append({"title": title, "error": str(exc)})

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
    }
