import os
import difflib
import re

import praw


def _normalize(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    # Strip leading articles
    for article in ("the ", "a ", "an "):
        if title.startswith(article):
            title = title[len(article):]
    return title


def get_reddit_client() -> praw.Reddit:
    """
    Build a read-only PRAW client from env vars.
    Raises EnvironmentError if credentials are missing.
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.environ.get("REDDIT_USER_AGENT", "games-investment-bot/1.0")

    if not client_id or not client_secret:
        raise EnvironmentError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set in .env. "
            "Register an app at https://www.reddit.com/prefs/apps"
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        read_only=True,
    )


def resolve_subreddit(reddit: praw.Reddit, game_title: str) -> str | None:
    """
    Search Reddit for a subreddit matching game_title.
    Returns the display_name of the best match if similarity >= 0.70, else None.
    """
    normalized = _normalize(game_title)
    try:
        results = list(reddit.subreddits.search(game_title, limit=5))
    except Exception:
        return None

    best_name = None
    best_ratio = 0.0

    for sub in results:
        candidate = _normalize(sub.display_name)
        ratio = difflib.SequenceMatcher(None, normalized, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = sub.display_name

    if best_ratio >= 0.70:
        return best_name
    return None


def fetch_reddit_posts(
    reddit: praw.Reddit,
    subreddit_name: str,
    limit: int = 50,
) -> list[dict]:
    """
    Fetch top posts from the past week in the given subreddit.
    Returns list of {"text": str, "score": int}.
    text = post title + first 500 chars of selftext.
    score = post upvote count (used as engagement weight).
    Skips posts with score < 1.
    """
    try:
        subreddit = reddit.subreddit(subreddit_name)
        posts = list(subreddit.top(time_filter="week", limit=limit))
    except Exception:
        return []

    result = []
    for post in posts:
        if post.score < 1:
            continue
        body = (post.selftext or "").strip()[:500]
        text = f"{post.title} {body}".strip()
        result.append({"text": text, "score": post.score})
    return result
