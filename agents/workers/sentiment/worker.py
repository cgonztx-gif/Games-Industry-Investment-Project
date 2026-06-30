"""
Sentiment Worker — Phase 2

Fetches Reddit posts and Steam reviews for every active watchlist game,
scores them with VADER + Claude Haiku ABSA, and writes one row per
(game_id, date, source) into sentiment_snapshots.

Prerequisite: apply database/migrations/001_sentiment_snapshots_unique.sql
in Supabase before first run.
"""

from datetime import date

from database.db_client import (
    get_client,
    get_watchlist_games,
    get_last_player_metrics,
    write_sentiment_snapshot,
)
from agents.workers.sentiment.reddit_client import (
    get_reddit_client,
    resolve_subreddit,
    fetch_reddit_posts,
)
from agents.workers.sentiment.steam_reviews_client import fetch_steam_reviews
from agents.workers.sentiment.vader_scorer import score_texts
from agents.workers.sentiment.absa_client import run_absa
from agents.workers.sentiment.divergence import compute_divergence

_MIN_TEXTS_FOR_ABSA = 5  # skip ABSA if fewer than this many reviews/posts


def run() -> dict:
    db = get_client()
    today = date.today().isoformat()
    games = get_watchlist_games(db)

    # Init Reddit once; degrade to Steam-only if credentials are missing
    reddit_enabled = False
    reddit = None
    try:
        reddit = get_reddit_client()
        reddit_enabled = True
        print("[sentiment] Reddit enabled.")
    except EnvironmentError as e:
        print(f"[sentiment] Reddit disabled — {e}")

    processed = []
    errors = []
    skipped_no_data = 0

    for i, game in enumerate(games):
        if i % 10 == 0:
            print(f"[sentiment] Progress: {i}/{len(games)}")

        game_id = game["game_id"]
        title = game.get("title", "unknown")
        steam_app_id = game.get("steam_app_id")
        wrote_any = False

        try:
            player_metrics = get_last_player_metrics(db, game_id)

            # ── Steam reviews ──────────────────────────────────────────────
            if steam_app_id:
                reviews = fetch_steam_reviews(steam_app_id, num_per_page=50)
                if reviews:
                    steam_score = score_texts(reviews)
                    texts = [r["text"] for r in reviews]
                    themes = (
                        run_absa(title, "steam", texts)
                        if len(reviews) >= _MIN_TEXTS_FOR_ABSA
                        else []
                    )
                    flag, note = compute_divergence(steam_score, player_metrics)
                    write_sentiment_snapshot(db, {
                        "game_id": game_id,
                        "date": today,
                        "source": "steam",
                        "sentiment_score": steam_score,
                        "top_themes": themes,
                        "divergence_flag": flag,
                        "vocal_minority_note": note,
                    })
                    wrote_any = True

            # ── Reddit posts ───────────────────────────────────────────────
            if reddit_enabled:
                subreddit_name = resolve_subreddit(reddit, title)
                if subreddit_name:
                    posts = fetch_reddit_posts(reddit, subreddit_name, limit=50)
                    if posts:
                        reddit_score = score_texts(posts)
                        texts = [p["text"] for p in posts]
                        themes = (
                            run_absa(title, "reddit", texts)
                            if len(posts) >= _MIN_TEXTS_FOR_ABSA
                            else []
                        )
                        flag, note = compute_divergence(reddit_score, player_metrics)
                        write_sentiment_snapshot(db, {
                            "game_id": game_id,
                            "date": today,
                            "source": "reddit",
                            "sentiment_score": reddit_score,
                            "top_themes": themes,
                            "divergence_flag": flag,
                            "vocal_minority_note": note,
                        })
                        wrote_any = True

            if not wrote_any:
                skipped_no_data += 1
            else:
                processed.append({"game_id": game_id, "title": title})

        except Exception as e:
            errors.append({"title": title, "error": str(e)})

    print(
        f"[sentiment] Done — {len(processed)} games written, "
        f"{skipped_no_data} skipped (no data), {len(errors)} errors."
    )
    return {
        "date": today,
        "games_processed": len(processed),
        "skipped_no_data": skipped_no_data,
        "error_count": len(errors),
        "errors": errors,
    }
