"""
Unit tests for agents/tracing.py.

No live LangSmith calls: configure_tracing()'s branching is pure env-var
logic, and traced_step() is exercised only through its no-op path (no
LANGSMITH_API_KEY set), which is the only behavior this repo's environments
can exercise without live credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.tracing import configure_tracing, traced_step


def test_configure_tracing_no_key_returns_false(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

    assert configure_tracing() is False
    # No env vars should be touched when the key is absent.
    import os

    assert "LANGSMITH_TRACING" not in os.environ
    assert "LANGSMITH_PROJECT" not in os.environ


def test_configure_tracing_with_key_sets_defaults(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

    assert configure_tracing() is True

    import os

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "games-industry-investment-platform"


def test_configure_tracing_respects_existing_project(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "custom-project")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    assert configure_tracing() is True

    import os

    assert os.environ["LANGSMITH_PROJECT"] == "custom-project"


def test_traced_step_no_key_calls_through(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    calls = []

    def fn():
        calls.append(1)
        return 42

    result = traced_step("some_step")(fn)()
    assert result == 42
    assert calls == [1]
