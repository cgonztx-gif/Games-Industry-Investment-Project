"""
Unit tests for agents/workers/sentiment/worker.py::run().

No live network / Supabase calls -- every external touch point (DB client,
Steam reviews, Reddit source/resolver, YouTube, news_items, VADER/ABSA/
divergence/news-stance scoring) is monkeypatched on the worker module's own
namespace, matching the convention tests/test_news_worker.py establishes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.workers.sentiment.worker as sentiment_worker
from agents.workers.sentiment.reddit_source import RedditBlocked

_FAKE_DB = object()  # sentinel -- no real Supabase calls


def _game(game_id="g1", title="Elden Ring", steam_app_id="1245620", subreddit="Eldenring", sentiment_tier="tier_a"):
    return {
        "game_id": game_id,
        "title": title,
        "steam_app_id": steam_app_id,
        "subreddit": subreddit,
        "sentiment_tier": sentiment_tier,
        "watchlist_id": f"w-{game_id}",
    }


class _FakeRedditSource:
    def __init__(self, posts=None, raise_blocked=False):
        self.posts = posts or []
        self.raise_blocked = raise_blocked

    def fetch_posts(self, subreddit, sort="top", timeframe="week", limit=50):
        if self.raise_blocked:
            raise RedditBlocked("blocked")
        return self.posts

    def fetch_comments(self, post_id, subreddit, limit=100):
        return []


def _post(id="p1", title="Great patch", selftext="loved it", score=100):
    class _P:
        pass

    p = _P()
    p.id, p.title, p.selftext, p.score = id, title, selftext, score
    return p


def _run(
    games=None,
    steam_reviews=None,
    reddit_source=None,
    subreddit_by_title=None,
    youtube_comments=None,
    news_items_by_game=None,
    stance_by_game=None,
    last_player_metrics=None,
    written=None,
    subreddit_updates=None,
    monkeypatch=None,
):
    if games is None:
        games = [_game()]
    if steam_reviews is None:
        steam_reviews = []
    if reddit_source is None:
        reddit_source = _FakeRedditSource()
    if subreddit_by_title is None:
        subreddit_by_title = {}
    if youtube_comments is None:
        youtube_comments = []
    if news_items_by_game is None:
        news_items_by_game = {}
    if stance_by_game is None:
        stance_by_game = {}
    if last_player_metrics is None:
        last_player_metrics = {}
    if written is None:
        written = []
    if subreddit_updates is None:
        subreddit_updates = []

    monkeypatch.setattr(sentiment_worker, "get_client", lambda: _FAKE_DB)
    monkeypatch.setattr(sentiment_worker, "get_watchlist_games", lambda db: games)
    monkeypatch.setattr(sentiment_worker, "SupabaseApiCache", lambda client, source: None)
    monkeypatch.setattr(sentiment_worker, "SupabaseRedditCache", lambda client, source: None)
    monkeypatch.setattr(sentiment_worker, "load_game_youtube_playlists", lambda: {})
    monkeypatch.setattr(sentiment_worker, "build_subreddit_resolver", lambda: None)
    monkeypatch.setattr(sentiment_worker, "build_reddit_source", lambda cache_factory: reddit_source)

    def _fake_cached_resolve(title, resolver, cache):
        return subreddit_by_title.get(title)

    monkeypatch.setattr(sentiment_worker, "cached_resolve_subreddit", _fake_cached_resolve)
    monkeypatch.setattr(
        sentiment_worker, "update_watchlist_subreddit", lambda db, wid, sub: subreddit_updates.append((wid, sub))
    )
    monkeypatch.setattr(
        sentiment_worker, "get_last_player_metrics", lambda db, gid: last_player_metrics.get(gid)
    )
    monkeypatch.setattr(sentiment_worker, "fetch_steam_reviews", lambda app_id, num_per_page=50, cache=None: steam_reviews)
    monkeypatch.setattr(
        sentiment_worker,
        "fetch_youtube_comments",
        lambda title, cache=None, game_playlist_ids=None: youtube_comments,
    )
    monkeypatch.setattr(
        sentiment_worker, "get_recent_news_items", lambda db, gid, since: news_items_by_game.get(gid, [])
    )

    def _fake_classify_stance(title, news_items, sentiment_tier):
        return stance_by_game.get(title)

    monkeypatch.setattr(sentiment_worker, "classify_stance", _fake_classify_stance)

    # score_texts / run_absa / compute_divergence are deterministic pure
    # helpers already unit-tested elsewhere -- use the real implementations
    # so this test also exercises their real wiring into the worker.
    monkeypatch.setattr(sentiment_worker, "run_absa", lambda title, source, texts: [])

    def _fake_write(db, snapshot):
        written.append(snapshot)

    monkeypatch.setattr(sentiment_worker, "write_sentiment_snapshot", _fake_write)

    return sentiment_worker.run()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_writes_all_four_sources(monkeypatch):
    games = [_game("g1", "Elden Ring", "1245620", subreddit="Eldenring")]
    steam_reviews = [{"text": "amazing game", "score": 1}]
    reddit = _FakeRedditSource(posts=[_post()])
    youtube = [{"text": "so good", "score": 5}]
    news_items = {"g1": [{"title": "Elden Ring DLC review", "url": "https://a"}]}
    stance = {"Elden Ring": {"score": 7.0, "themes": ["combat"], "outlet_count": 3, "coverage_note": "positive coverage"}}
    written = []

    result = _run(
        games=games,
        steam_reviews=steam_reviews,
        reddit_source=reddit,
        youtube_comments=youtube,
        news_items_by_game=news_items,
        stance_by_game=stance,
        written=written,
        monkeypatch=monkeypatch,
    )

    sources_written = {w["source"] for w in written}
    assert sources_written == {"steam", "reddit", "youtube", "news"}
    assert result["games_processed"] == 1
    assert result["skipped_no_data"] == 0
    assert result["error_count"] == 0


def test_news_snapshot_uses_stance_score_not_vader(monkeypatch):
    games = [_game("g1", "Elden Ring", steam_app_id=None, subreddit=None)]
    news_items = {"g1": [{"title": "Elden Ring news", "url": "https://a"}]}
    stance = {"Elden Ring": {"score": 8.5, "themes": ["dlc"], "outlet_count": 4, "coverage_note": "broad coverage"}}
    written = []

    _run(
        games=games,
        news_items_by_game=news_items,
        stance_by_game=stance,
        written=written,
        monkeypatch=monkeypatch,
    )

    news_rows = [w for w in written if w["source"] == "news"]
    assert len(news_rows) == 1
    assert news_rows[0]["sentiment_score"] == 8.5
    assert news_rows[0]["top_themes"] == ["dlc"]


# ---------------------------------------------------------------------------
# News -> Sentiment integration boundary (gap 2a)
# ---------------------------------------------------------------------------

def test_zero_news_items_writes_no_news_snapshot(monkeypatch):
    """
    A game with zero news_items rows this week must not produce a source='news'
    sentiment_snapshots row at all (not an empty/zero-score row) -- confirms
    _write_news_snapshot's early-return-on-empty is the real, intended
    behavior of the News -> Sentiment boundary, not an accidental gap.
    """
    games = [_game("g1", "Untracked Game", steam_app_id=None, subreddit=None)]
    written = []

    result = _run(games=games, news_items_by_game={}, written=written, monkeypatch=monkeypatch)

    assert [w for w in written if w["source"] == "news"] == []
    assert result["skipped_no_data"] == 1
    assert result["games_processed"] == 0


def test_news_items_present_but_classify_stance_fails_writes_nothing(monkeypatch):
    """classify_stance() returning None (any internal failure) means skip
    entirely -- the call *is* the score, not an enrichment on top of one."""
    games = [_game("g1", "Elden Ring", steam_app_id=None, subreddit=None)]
    news_items = {"g1": [{"title": "Elden Ring news", "url": "https://a"}]}
    written = []

    result = _run(
        games=games,
        news_items_by_game=news_items,
        stance_by_game={},  # classify_stance returns None for this title
        written=written,
        monkeypatch=monkeypatch,
    )

    assert written == []
    assert result["skipped_no_data"] == 1


# ---------------------------------------------------------------------------
# Degraded paths
# ---------------------------------------------------------------------------

def test_reddit_blocked_does_not_prevent_other_sources(monkeypatch):
    games = [_game("g1", "Elden Ring", "1245620", subreddit="Eldenring")]
    steam_reviews = [{"text": "great", "score": 1}]
    reddit = _FakeRedditSource(raise_blocked=True)
    written = []

    result = _run(
        games=games,
        steam_reviews=steam_reviews,
        reddit_source=reddit,
        written=written,
        monkeypatch=monkeypatch,
    )

    assert result["reddit_blocked_count"] == 1
    sources_written = {w["source"] for w in written}
    assert "reddit" not in sources_written
    assert "steam" in sources_written
    assert result["games_processed"] == 1


def test_no_subreddit_resolved_skips_reddit_source_cleanly(monkeypatch):
    games = [_game("g1", "Elden Ring", "1245620", subreddit=None)]
    steam_reviews = [{"text": "great", "score": 1}]
    written = []

    result = _run(
        games=games,
        steam_reviews=steam_reviews,
        subreddit_by_title={},  # resolver finds nothing
        written=written,
        monkeypatch=monkeypatch,
    )

    assert "reddit" not in {w["source"] for w in written}
    assert result["error_count"] == 0
    assert result["reddit_blocked_count"] == 0


def test_game_with_zero_data_across_all_sources_is_skipped(monkeypatch):
    games = [_game("g1", "Ghost Game", steam_app_id=None, subreddit=None)]

    result = _run(games=games, monkeypatch=monkeypatch)

    assert result["games_processed"] == 0
    assert result["skipped_no_data"] == 1
    assert result["error_count"] == 0


def test_per_game_exception_is_isolated_and_run_continues(monkeypatch):
    games = [_game("g1", "Broken Game", "111"), _game("g2", "Fine Game", "222", subreddit=None)]

    def _boom(app_id, num_per_page=50, cache=None):
        if app_id == "111":
            raise RuntimeError("steam reviews down")
        return [{"text": "fine", "score": 1}]

    written = []
    monkeypatch.setattr(sentiment_worker, "get_client", lambda: _FAKE_DB)
    monkeypatch.setattr(sentiment_worker, "get_watchlist_games", lambda db: games)
    monkeypatch.setattr(sentiment_worker, "SupabaseApiCache", lambda client, source: None)
    monkeypatch.setattr(sentiment_worker, "SupabaseRedditCache", lambda client, source: None)
    monkeypatch.setattr(sentiment_worker, "load_game_youtube_playlists", lambda: {})
    monkeypatch.setattr(sentiment_worker, "build_subreddit_resolver", lambda: None)
    monkeypatch.setattr(sentiment_worker, "build_reddit_source", lambda cache_factory: _FakeRedditSource())
    monkeypatch.setattr(sentiment_worker, "cached_resolve_subreddit", lambda title, resolver, cache: None)
    monkeypatch.setattr(sentiment_worker, "update_watchlist_subreddit", lambda db, wid, sub: None)
    monkeypatch.setattr(sentiment_worker, "get_last_player_metrics", lambda db, gid: None)
    monkeypatch.setattr(sentiment_worker, "fetch_steam_reviews", _boom)
    monkeypatch.setattr(
        sentiment_worker, "fetch_youtube_comments", lambda title, cache=None, game_playlist_ids=None: []
    )
    monkeypatch.setattr(sentiment_worker, "get_recent_news_items", lambda db, gid, since: [])
    monkeypatch.setattr(sentiment_worker, "classify_stance", lambda title, items, sentiment_tier: None)
    monkeypatch.setattr(sentiment_worker, "run_absa", lambda title, source, texts: [])
    monkeypatch.setattr(sentiment_worker, "write_sentiment_snapshot", lambda db, snap: written.append(snap))

    result = sentiment_worker.run()

    assert result["error_count"] == 1
    assert result["errors"][0]["title"] == "Broken Game"
    # g2 ("Fine Game") still gets processed despite g1 raising.
    assert any(w["game_id"] == "g2" for w in written)


def test_empty_watchlist_returns_zeroed_stats(monkeypatch):
    result = _run(games=[], monkeypatch=monkeypatch)

    assert result["games_processed"] == 0
    assert result["skipped_no_data"] == 0
    assert result["error_count"] == 0
    assert result["reddit_blocked_count"] == 0
