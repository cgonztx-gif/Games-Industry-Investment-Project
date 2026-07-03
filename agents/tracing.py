"""
LangSmith tracing helpers for the weekly pipeline.

Fully opt-in: with no `LANGSMITH_API_KEY` set, `configure_tracing()` is a no-op
and `traced_step()` wraps callables with `langsmith.traceable`, which itself
no-ops (zero network calls) whenever tracing is disabled. Nothing here changes
pipeline behavior or output when LangSmith is not configured.
"""

import os

from langsmith import traceable


def configure_tracing() -> bool:
    """
    Enable LangSmith tracing for this process if `LANGSMITH_API_KEY` is set.

    Returns True if tracing was enabled, False (and does nothing else) if the
    API key is unset.
    """
    if not os.environ.get("LANGSMITH_API_KEY"):
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", "games-industry-investment-platform")
    return True


def traced_step(name: str, run_type: str = "tool"):
    """
    Return a `traceable` decorator for wrapping an existing callable at its
    call site, e.g. `traced_step("market_player_worker")(market_worker.run)()`.
    """
    return traceable(name=name, run_type=run_type)
