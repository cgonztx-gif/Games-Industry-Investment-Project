"""
Integration-style test for run_weekly.py's own call-order wiring (never
exercised before this pass -- run_weekly.py is a plain top-level script, not
a function-based module, so this executes it via runpy with every worker's
`run()`/`build_trade_plan()`/etc. monkeypatched to a call-order-recording
fake. No live network / Supabase / Anthropic / CrewAI calls.

`crewai` is not installed in this environment (it's in requirements.txt but
absent here -- confirmed by a bare `import crewai` failing), so a minimal
fake module is injected into sys.modules before agents.orchestrator.crew is
imported; this is the only way to exercise run_weekly.py's actual
try/except-around-the-CrewAI-step behavior at all without installing a real
dependency into the environment.
"""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _install_fake_crewai(monkeypatch, kickoff_fn):
    fake = types.ModuleType("crewai")

    class _Agent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Task:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Process:
        sequential = "sequential"

    class _Crew:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def kickoff(self):
            return kickoff_fn()

    fake.Agent = _Agent
    fake.Task = _Task
    fake.Crew = _Crew
    fake.Process = _Process
    monkeypatch.setitem(sys.modules, "crewai", fake)


def _run_weekly(monkeypatch, kickoff_fn, call_order: list[str], argv: list[str] | None = None):
    """
    Pre-import and monkeypatch every worker/agent entrypoint on its own module
    object, then execute run_weekly.py via runpy. Its `from X import Y as Z`
    statements resolve against the already-imported (and now-patched) modules
    in sys.modules, so the patches take effect inside the executed script.

    sys.argv must be replaced: runpy executes with the real process argv,
    which under pytest holds pytest's own arguments -- run_weekly.py's
    argparse would reject them with SystemExit(2).
    """
    monkeypatch.setattr(sys, "argv", ["run_weekly.py", *(argv or [])])
    _install_fake_crewai(monkeypatch, kickoff_fn)

    import agents.orchestrator.crew as crew_module  # noqa: F401 -- built against the fake crewai
    import agents.synthesis.agent as synthesis_agent
    import agents.workers.financial_overlay.worker as financial_worker
    import agents.workers.market_player.worker as market_worker
    import agents.workers.patch_notes.worker as patch_notes_worker
    import agents.workers.studio_intel.worker as studio_intel_worker
    import agents.workers.news.worker as news_worker
    import agents.workers.sentiment.worker as sentiment_worker
    import agents.workers.discovery.worker as discovery_worker
    import agents.portfolio.manager as portfolio_manager
    import agents.portfolio.execution_agent as execution_agent
    import agents.portfolio.returns_tracker as returns_tracker

    def _record(name, result):
        def _fake(*_a, **_kw):
            call_order.append(name)
            return result

        return _fake

    monkeypatch.setattr(
        market_worker, "run", _record("market_player", {"top_10_by_ccu": [{"title": "G", "ccu": 100, "review_score": 90.0}]})
    )
    monkeypatch.setattr(financial_worker, "run", _record("financial_overlay", {}))
    monkeypatch.setattr(studio_intel_worker, "run", _record("studio_intel", {}))
    monkeypatch.setattr(
        patch_notes_worker, "run", _record("patch_notes", {"events_written": 0, "error_count": 0})
    )
    monkeypatch.setattr(
        news_worker,
        "run",
        _record("news", {"items_written": 0, "articles_fetched": 0, "entities_with_coverage": 0}),
    )
    monkeypatch.setattr(
        sentiment_worker,
        "run",
        _record("sentiment", {"games_processed": 0, "error_count": 0, "reddit_blocked_count": 0}),
    )
    monkeypatch.setattr(
        discovery_worker,
        "run",
        _record(
            "discovery",
            {"proposals_written": 0, "game_level_count": 0, "company_level_count": 0, "error_count": 0},
        ),
    )
    monkeypatch.setattr(
        synthesis_agent, "run", _record("synthesis", {"divergence_count": 0, "risk_count": 0})
    )
    monkeypatch.setattr(
        portfolio_manager,
        "build_trade_plan",
        _record("portfolio_manager", {"week_of": "2026-07-15", "order_count": 0, "watch_count": 0}),
    )
    monkeypatch.setattr(
        execution_agent,
        "run",
        _record("execution_agent", {"orders_checked": 0, "orders_placed": 0, "error_count": 0}),
    )
    monkeypatch.setattr(
        returns_tracker,
        "run",
        _record("returns_tracker", {"total_value": 0, "total_return_pct": 0, "benchmark_return_pct": 0}),
    )

    original_kickoff = kickoff_fn

    def _wrapped_kickoff():
        call_order.append("crew_kickoff")
        return original_kickoff()

    _install_fake_crewai(monkeypatch, _wrapped_kickoff)
    # Re-point the already-constructed crew's kickoff too, in case
    # agents.orchestrator.crew was already imported by an earlier test in
    # this process (module import is cached across the whole test session).
    monkeypatch.setattr(crew_module.games_intel_crew, "kickoff", _wrapped_kickoff)

    runpy.run_path(str(_ROOT / "run_weekly.py"), run_name="__main__")


# ---------------------------------------------------------------------------
# Call order matches CLAUDE.md's documented pipeline order
# ---------------------------------------------------------------------------

_EXPECTED_ORDER = [
    "market_player",
    "financial_overlay",
    "studio_intel",
    "patch_notes",
    "news",
    "sentiment",
    "discovery",
    "synthesis",
    "portfolio_manager",
    "execution_agent",
    "returns_tracker",
    "crew_kickoff",
]


def test_run_weekly_calls_workers_in_documented_order(monkeypatch):
    call_order: list[str] = []

    _run_weekly(monkeypatch, kickoff_fn=lambda: "crew ok", call_order=call_order)

    assert call_order == _EXPECTED_ORDER


def test_crewai_placeholder_failure_does_not_crash_the_run(monkeypatch):
    """
    Per CLAUDE.md: the CrewAI placeholder pipeline step is wrapped so a
    failure there is non-fatal -- the weekly briefing has already been
    written by that point. Verify this is actually true in code, not just
    documented: runpy.run_path must complete without raising even though
    the crew's kickoff() call always raises.
    """
    call_order: list[str] = []

    def _boom():
        raise RuntimeError("CrewAI blew up")

    _run_weekly(monkeypatch, kickoff_fn=_boom, call_order=call_order)

    # The run reached and attempted the crew step (it's last), and did not
    # raise out of runpy.run_path despite the kickoff failure.
    assert call_order == _EXPECTED_ORDER


# ---------------------------------------------------------------------------
# --phase selection (one GitHub Actions job per phase; see weekly.yml)
# ---------------------------------------------------------------------------

_PHASE_EXPECTED = {
    "collect": ["market_player", "financial_overlay", "studio_intel", "patch_notes"],
    "news": ["news"],
    "sentiment": ["sentiment"],
    "synthesize": [
        "discovery",
        "synthesis",
        "portfolio_manager",
        "execution_agent",
        "returns_tracker",
        "crew_kickoff",
    ],
}


@pytest.mark.parametrize("phase", list(_PHASE_EXPECTED))
def test_phase_flag_runs_exactly_that_phases_steps_in_order(monkeypatch, phase):
    call_order: list[str] = []

    _run_weekly(
        monkeypatch, kickoff_fn=lambda: "crew ok", call_order=call_order, argv=["--phase", phase]
    )

    assert call_order == _PHASE_EXPECTED[phase]


def test_phases_concatenated_cover_the_full_documented_order():
    # Guards against a step being dropped from every phase: running the four
    # phases back-to-back must be exactly the full no-args pipeline.
    concatenated = [
        step
        for phase in ["collect", "news", "sentiment", "synthesize"]
        for step in _PHASE_EXPECTED[phase]
    ]
    assert concatenated == _EXPECTED_ORDER


def test_unknown_phase_exits_without_calling_any_worker(monkeypatch):
    call_order: list[str] = []

    with pytest.raises(SystemExit):
        _run_weekly(
            monkeypatch, kickoff_fn=lambda: "crew ok", call_order=call_order, argv=["--phase", "bogus"]
        )

    assert call_order == []


def test_crewai_failure_is_non_fatal_in_the_synthesize_phase_too(monkeypatch):
    call_order: list[str] = []

    def _boom():
        raise RuntimeError("CrewAI blew up")

    _run_weekly(monkeypatch, kickoff_fn=_boom, call_order=call_order, argv=["--phase", "synthesize"])

    assert call_order == _PHASE_EXPECTED["synthesize"]
