"""
Integration-style tests for agents/synthesis/agent.py::run() -- the actual
entrypoint had zero direct test coverage before this pass (only its pure
internal helpers, e.g. _compute_divergence/_sentiment_by_game, were tested).

No live network / Supabase / Anthropic calls: get_client, get_weekly_outputs,
write_weekly_briefing, send_briefing_email, and _dispatch_deep_dives are all
monkeypatched on the module's own namespace (matching tests/test_news_worker.py's
convention). _dispatch_deep_dives specifically must be patched as a module
attribute (not via its default `deep_dive_fn` parameter) since run() calls it
with no deep_dive_fn override -- the default is bound to the real
agents.synthesis.deep_dive.run_deep_dive at import time and re-patching
agent.run_deep_dive afterward would not affect that already-bound default.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agents.synthesis.agent as agent

_FAKE_DB = object()  # sentinel -- no real Supabase calls


class _FakeGamesTable:
    def __init__(self, titles: dict[str, str]):
        self._titles = titles
        self._ids: list[str] = []

    def select(self, *_a, **_kw):
        return self

    def in_(self, _col, ids):
        self._ids = ids
        return self

    def execute(self):
        class _Result:
            pass

        r = _Result()
        r.data = [{"game_id": gid, "title": self._titles[gid]} for gid in self._ids if gid in self._titles]
        return r


class _FakeDb:
    """Only supports the one .table("games")... chain _game_titles() needs."""

    def __init__(self, titles: dict[str, str] | None = None):
        self._titles = titles or {}

    def table(self, name):
        assert name == "games"
        return _FakeGamesTable(self._titles)


def _player_metric(game_id, ccu=1000, review_velocity=10):
    return {"game_id": game_id, "concurrent_players": ccu, "review_velocity": review_velocity}


def _sentiment_row(game_id, source, score):
    return {"game_id": game_id, "source": source, "sentiment_score": score, "top_themes": []}


def _patch_event(game_id, cadence_status="on_pace"):
    return {"game_id": game_id, "date": "2026-07-10", "cadence_status": cadence_status}


def _studio_signal(studio_id, severity="high", signal_type="layoffs"):
    return {
        "studio_id": studio_id,
        "date": "2026-07-10",
        "severity": severity,
        "signal_type": signal_type,
        "description": "test signal",
    }


def _run(
    outputs,
    game_titles=None,
    written=None,
    email_calls=None,
    email_raises=False,
    dispatch_calls=None,
    monkeypatch=None,
):
    if written is None:
        written = []
    if email_calls is None:
        email_calls = []
    if dispatch_calls is None:
        dispatch_calls = []

    monkeypatch.setattr(agent, "get_client", lambda: _FakeDb(game_titles or {}))
    monkeypatch.setattr(agent, "get_weekly_outputs", lambda db, run_date, week_start: outputs)

    def _fake_write(db, briefing):
        written.append(briefing)

    monkeypatch.setattr(agent, "write_weekly_briefing", _fake_write)

    def _fake_dispatch(divergences, risks, game_titles_arg, deep_dive_fn=None, max_dives=2):
        dispatch_calls.append((divergences, risks, game_titles_arg))
        return []

    monkeypatch.setattr(agent, "_dispatch_deep_dives", _fake_dispatch)

    def _fake_email(briefing):
        email_calls.append(briefing)
        if email_raises:
            raise RuntimeError("resend down")

    monkeypatch.setattr(agent, "send_briefing_email", _fake_email)

    return agent.run(run_date="2026-07-15")


# ---------------------------------------------------------------------------
# Realistic multi-table combination
# ---------------------------------------------------------------------------

def test_run_combines_all_five_tables_into_briefing(monkeypatch):
    outputs = {
        "player_metrics": [
            _player_metric("g1", ccu=5000, review_velocity=20),
            _player_metric("g2", ccu=100, review_velocity=-5),
        ],
        "sentiment": [
            _sentiment_row("g1", "reddit", 7.0),
            _sentiment_row("g1", "steam", 7.5),
            _sentiment_row("g2", "reddit", 2.0),
            _sentiment_row("g2", "steam", 3.0),
        ],
        "patch_events": [_patch_event("g1")],
        "studio_signals": [_studio_signal("s1", severity="high")],
        "equity_signals": [{"ticker": "EA", "date": "2026-07-15"}],
    }
    written = []

    result = _run(outputs, written=written, monkeypatch=monkeypatch)

    assert len(written) == 1
    briefing = written[0]
    assert briefing["week_of"] == "2026-07-15"
    assert briefing["portfolio_update"]["equity_signals_count"] == 1
    assert briefing["notable_events"]["patch_events"] == 1
    assert briefing["notable_events"]["studio_signals"] == 1
    # g2: sentiment_score 2.5 avg <= 3.5, review_velocity -5 < 0, no patch -> high risk
    risk_game_ids = {r.get("game_id") for r in briefing["risk_flags"]}
    assert "g2" in risk_game_ids
    # All 5 layers present -> "medium" confidence per _confidence()'s >=4 threshold
    assert result["confidence"] == "medium"
    assert result["divergence_count"] == briefing["portfolio_update"]["divergence_count"]
    assert result["risk_count"] == briefing["portfolio_update"]["risk_count"]


def test_run_bearish_divergence_appears_in_top_opportunities(monkeypatch):
    outputs = {
        "player_metrics": [_player_metric("g1", ccu=5000, review_velocity=20)],
        "sentiment": [_sentiment_row("g1", "reddit", 2.0), _sentiment_row("g1", "steam", 3.0)],
        "patch_events": [],
        "studio_signals": [],
        "equity_signals": [],
    }
    written = []

    result = _run(outputs, written=written, monkeypatch=monkeypatch)

    briefing = written[0]
    assert len(briefing["top_opportunities"]) == 1
    assert briefing["top_opportunities"][0]["type"] == "bearish_text_stable_quant"
    assert result["opportunity_count"] == 1


# ---------------------------------------------------------------------------
# Zero-rows-in-every-table path (never exercised before this pass)
# ---------------------------------------------------------------------------

def test_run_with_zero_rows_in_every_table_still_writes_a_briefing(monkeypatch):
    outputs = {
        "player_metrics": [],
        "sentiment": [],
        "patch_events": [],
        "studio_signals": [],
        "equity_signals": [],
    }
    written = []

    result = _run(outputs, written=written, monkeypatch=monkeypatch)

    assert len(written) == 1
    briefing = written[0]
    assert briefing["top_opportunities"] == []
    assert briefing["risk_flags"] == []
    assert briefing["notable_events"] == {"patch_events": 0, "studio_signals": 0}
    assert result["confidence"] == "very_low"
    assert result["divergence_count"] == 0
    assert result["risk_count"] == 0
    assert result["opportunity_count"] == 0


def test_run_with_only_player_metrics_present_is_low_confidence(monkeypatch):
    outputs = {
        "player_metrics": [_player_metric("g1")],
        "sentiment": [],
        "patch_events": [],
        "studio_signals": [],
        "equity_signals": [],
    }
    written = []

    result = _run(outputs, written=written, monkeypatch=monkeypatch)

    # Only 1 of 5 layers present -> _confidence()'s <2 branch -> "very_low"
    assert result["confidence"] == "very_low"


# ---------------------------------------------------------------------------
# Deep-dive dispatch wiring (must be called from run(), not just tested in
# isolation against _dispatch_deep_dives directly)
# ---------------------------------------------------------------------------

def test_run_invokes_dispatch_deep_dives_with_bearish_game_ids(monkeypatch):
    outputs = {
        "player_metrics": [_player_metric("g1", ccu=5000, review_velocity=20)],
        "sentiment": [_sentiment_row("g1", "reddit", 2.0), _sentiment_row("g1", "steam", 3.0)],
        "patch_events": [],
        "studio_signals": [_studio_signal("s1", severity="high")],
        "equity_signals": [],
    }
    dispatch_calls = []

    _run(
        outputs,
        game_titles={"g1": "Elden Ring"},
        dispatch_calls=dispatch_calls,
        monkeypatch=monkeypatch,
    )

    assert len(dispatch_calls) == 1
    divergences, risks, game_titles_arg = dispatch_calls[0]
    assert any(d["type"] == "bearish_text_stable_quant" for d in divergences)
    assert game_titles_arg.get("g1") == "Elden Ring"


def test_run_deep_dive_dispatch_failure_is_non_fatal(monkeypatch):
    outputs = {
        "player_metrics": [],
        "sentiment": [],
        "patch_events": [],
        "studio_signals": [],
        "equity_signals": [],
    }
    written = []

    def _boom(*_a, **_kw):
        raise RuntimeError("dispatch exploded")

    monkeypatch.setattr(agent, "get_client", lambda: _FakeDb())
    monkeypatch.setattr(agent, "get_weekly_outputs", lambda db, run_date, week_start: outputs)
    monkeypatch.setattr(agent, "write_weekly_briefing", lambda db, briefing: written.append(briefing))
    monkeypatch.setattr(agent, "_dispatch_deep_dives", _boom)
    monkeypatch.setattr(agent, "send_briefing_email", lambda briefing: None)

    result = agent.run(run_date="2026-07-15")

    assert len(written) == 1
    assert result["confidence"] == "very_low"


# ---------------------------------------------------------------------------
# Email delivery is non-fatal (per agent.py's own try/except around it)
# ---------------------------------------------------------------------------

def test_run_email_failure_does_not_prevent_briefing_being_written(monkeypatch):
    outputs = {
        "player_metrics": [],
        "sentiment": [],
        "patch_events": [],
        "studio_signals": [],
        "equity_signals": [],
    }
    written = []
    email_calls = []

    result = _run(
        outputs,
        written=written,
        email_calls=email_calls,
        email_raises=True,
        monkeypatch=monkeypatch,
    )

    assert len(written) == 1
    assert len(email_calls) == 1
    assert result is not None
