"""
Per-subagent token-spend logging, independent of LangSmith.

Mirrors agents/tracing.py's pattern: each worker/agent step in run_weekly.py is
wrapped at the call site (`token_tracked_step(name)(callable)()`), not via a
decorator inside each worker file, so no worker module needs to import this
directly. The individual Anthropic call sites (absa_client.py,
claude_rationale.py, news_stance_client.py, entity_matcher.py, deep_dive.py)
and the Agent SDK session in portfolio/manager.py each call `record_usage(...)`
right after receiving a response.

A thread-local stack of frames lets nested/sequential steps in the same
process accumulate independently -- record_usage() always credits the
innermost currently-open step. Calls made outside any tracked step (e.g. ad
hoc worker invocations from a REPL) are silently dropped rather than raising,
since this is a logging convenience, not a correctness-critical path. Fully
in-process and synchronous: no DB writes, no network calls, zero cost when no
step is open.
"""

from __future__ import annotations

import threading

_local = threading.local()


def _stack() -> list[dict]:
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


def record_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """Credit token usage to the innermost open `token_tracked_step`, if any."""
    stack = _stack()
    if not stack:
        return
    frame = stack[-1]
    frame["input_tokens"] += input_tokens or 0
    frame["output_tokens"] += output_tokens or 0
    frame["calls"] += 1
    frame["models"][model] = frame["models"].get(model, 0) + 1


def record_usage_from_message(model: str, message) -> None:
    """
    Same as `record_usage`, reading token counts off a real `anthropic.Anthropic`
    `messages.create()` response. Defensive against `usage` being absent (unit
    tests commonly stub a minimal fake message with no `.usage` at all) --
    silently records zero rather than raising, since token-spend logging must
    never be the reason a real LLM call site's try/except swallows a result.
    """
    usage = getattr(message, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage is not None else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage is not None else 0
    record_usage(model, input_tokens, output_tokens)


def token_tracked_step(name: str):
    """
    Return a decorator that wraps a callable, printing its token spend on exit.

    Prints nothing if the wrapped call never recorded any usage (e.g. workers
    with no LLM call, or an LLM call that errored before a response arrived).
    """

    def decorator(fn):
        def inner(*args, **kwargs):
            stack = _stack()
            frame = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "models": {}}
            stack.append(frame)
            try:
                return fn(*args, **kwargs)
            finally:
                stack.pop()
                if frame["calls"]:
                    models = ", ".join(f"{m} x{c}" for m, c in frame["models"].items())
                    print(
                        f"[token-spend] {name}: {frame['calls']} call(s), "
                        f"{frame['input_tokens']} input / {frame['output_tokens']} output "
                        f"tokens ({models})"
                    )

        return inner

    return decorator
