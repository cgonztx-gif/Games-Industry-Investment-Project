"""
Unit tests for agents/portfolio/manager.py (Claude Agent SDK + MCP migration).

No live network and no real Anthropic/Supabase/Alpaca/CLI calls. build_trade_plan()
takes an injectable ``run_agent_fn`` (the LLM-session seam that replaced the old
``client``) plus function overrides for get_latest_weekly_briefing /
get_account_state / write_trade_plan / write_trade_order, so every test uses fakes
with scripted behavior (same dependency-injection style as tests/test_deep_dive.py
and agents/synthesis/agent.py's `_dispatch_deep_dives(deep_dive_fn=...)`).

The account tool's graceful-degradation behaviour is unit-tested directly against
``agents.portfolio.alpaca_mcp.account_state_text`` -- in the migrated architecture
account state is delivered through the read-only MCP tool result, not pre-fetched
into the user prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.portfolio.alpaca_mcp import account_state_text
from agents.portfolio.manager import build_trade_plan

_FAKE_DB = "fake-db-sentinel"  # never touched directly; only passed through to fakes


class FakeAgentRunner:
    """
    Stands in for the real Agent SDK session. Records the prompts/config it was
    called with and returns scripted assistant text (or raises), mirroring the old
    FakeAnthropicClient but for the ``run_agent_fn(system_prompt=..., user_prompt=
    ..., mcp_servers=..., allowed_tools=..., model=...) -> str | None`` seam.
    """

    def __init__(self, response_text=None, exc=None):
        self._text = response_text
        self._exc = exc
        self.last_kwargs = None

    def __call__(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc:
            raise self._exc
        return self._text


_FAKE_BRIEFING = {
    "id": "briefing-1",
    "week_of": "2026-06-29",
    "briefing_text": "Weekly briefing for 2026-06-29: confidence=medium.",
    "portfolio_update": {"confidence": "medium"},
    "top_opportunities": [{"game_id": "g1", "type": "bearish_text_stable_quant"}],
    "risk_flags": [{"game_id": "g1", "severity": "high"}],
    "notable_events": {"patch_events": 2},
}

_VALID_PLAN_JSON = """\
{
  "week_of": "2026-06-29",
  "portfolio_risk_posture": "balanced",
  "cash_buffer_target_pct": 15,
  "benchmark": "SPY",
  "orders": [
    {
      "ticker": "TTWO",
      "action": "buy",
      "target_weight_pct": 3.0,
      "size_usd": 3000.0,
      "conviction_tier": "core",
      "rationale": "Multi-layer convergence.",
      "thesis_invalidation": "Close if decline persists 2 weeks.",
      "risk_checks": {"single_ticker_limit_ok": true, "sector_limit_ok": true, "cash_buffer_ok": true}
    },
    {
      "ticker": "BADORDER",
      "action": "explode",
      "target_weight_pct": 1.0,
      "size_usd": 100.0,
      "conviction_tier": "starter",
      "rationale": "Malformed action should be skipped.",
      "thesis_invalidation": "n/a",
      "risk_checks": {}
    }
  ],
  "rejected_or_watch": [
    {"ticker": "MSFT", "reason": "Game signal immaterial to mega-cap parent."}
  ]
}
"""


class _FakeDBWriters:
    """Records write_trade_plan / write_trade_order calls for assertions."""

    def __init__(self, plan_id: str = "plan-123"):
        self.plan_id = plan_id
        self.plan_calls: list[tuple] = []
        self.order_calls: list[tuple] = []

    def write_trade_plan(self, db, plan: dict) -> str:
        self.plan_calls.append((db, plan))
        return self.plan_id

    def write_trade_order(self, db, order: dict) -> None:
        self.order_calls.append((db, order))


def _get_briefing_ok(db):
    return dict(_FAKE_BRIEFING)


def _account_state_ok():
    return {"cash": 10000.0, "buying_power": 20000.0, "portfolio_value": 50000.0, "positions": []}


def test_no_briefing_returns_none():
    writers = _FakeDBWriters()
    runner = FakeAgentRunner(response_text=_VALID_PLAN_JSON)

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=lambda db: None,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None
    assert writers.plan_calls == []
    assert writers.order_calls == []
    assert runner.last_kwargs is None  # never reached the LLM session


def test_valid_plan_writes_plan_and_orders():
    writers = _FakeDBWriters(plan_id="plan-abc")
    runner = FakeAgentRunner(response_text=_VALID_PLAN_JSON)

    result = build_trade_plan(
        run_date="2026-06-29",
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result == {
        "week_of": "2026-06-29",
        "plan_id": "plan-abc",
        "order_count": 1,  # the malformed "explode" action order is skipped
        "watch_count": 1,
    }

    assert len(writers.plan_calls) == 1
    _, plan_row = writers.plan_calls[0]
    assert plan_row["week_of"] == "2026-06-29"
    assert plan_row["briefing_id"] == "briefing-1"
    assert "status" not in plan_row  # must stay at schema default 'pending'
    assert "TTWO" in plan_row["claude_rationale"]

    assert len(writers.order_calls) == 1
    _, order_row = writers.order_calls[0]
    assert order_row["plan_id"] == "plan-abc"
    assert order_row["ticker"] == "TTWO"
    assert order_row["action"] == "buy"
    assert order_row["size_usd"] == 3000.0
    assert "status" not in order_row  # must stay at schema default 'pending', never 'approved'

    # The session got the skill content as system prompt + briefing content in the user
    # prompt, and was pinned to exactly the read-only Alpaca tool + the Opus model.
    assert "Output Contract" in runner.last_kwargs["system_prompt"]
    assert "2026-06-29" in runner.last_kwargs["user_prompt"]
    assert runner.last_kwargs["allowed_tools"] == [
        "mcp__alpaca-readonly__get_account_state"
    ]
    assert runner.last_kwargs["model"] == "claude-opus-4-8"
    assert "alpaca-readonly" in runner.last_kwargs["mcp_servers"]


def test_account_state_unavailable_still_produces_plan():
    writers = _FakeDBWriters(plan_id="plan-xyz")
    runner = FakeAgentRunner(response_text=_VALID_PLAN_JSON)

    def _account_state_fails():
        raise RuntimeError("no credentials")

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_fails,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    # A failing account fetch must not fail the run: the read-only tool degrades to
    # an "UNAVAILABLE -- build conservatively" note, and a plan is still produced.
    assert result is not None
    assert result["plan_id"] == "plan-xyz"


def test_account_state_text_degrades_gracefully_on_failure():
    """The graceful-degradation note (formerly inlined in the user prompt) now lives
    in the read-only MCP tool's result text -- assert it directly."""

    def _account_state_fails():
        raise RuntimeError("no credentials")

    text = account_state_text(_account_state_fails)
    assert "UNAVAILABLE" in text
    assert "no credentials" in text


def test_account_state_text_returns_json_on_success():
    text = account_state_text(_account_state_ok)
    parsed = json.loads(text)
    assert parsed["cash"] == 10000.0


def test_refusal_or_error_returns_none():
    # The default runner returns None on a refusal/error; model that here.
    writers = _FakeDBWriters()
    runner = FakeAgentRunner(response_text=None)

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None
    assert writers.plan_calls == []
    assert writers.order_calls == []


def test_malformed_json_returns_none():
    writers = _FakeDBWriters()
    runner = FakeAgentRunner(response_text="not valid json")

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None
    assert writers.plan_calls == []


def test_missing_orders_key_returns_none():
    writers = _FakeDBWriters()
    runner = FakeAgentRunner(response_text='{"portfolio_risk_posture": "balanced"}')

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None


def test_runner_exception_returns_none():
    writers = _FakeDBWriters()
    runner = FakeAgentRunner(exc=RuntimeError("network error"))

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None
    assert writers.plan_calls == []


def test_markdown_fenced_json_is_stripped():
    writers = _FakeDBWriters(plan_id="plan-fenced")
    fenced = "```json\n" + _VALID_PLAN_JSON + "\n```"
    runner = FakeAgentRunner(response_text=fenced)

    result = build_trade_plan(
        run_agent_fn=runner,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is not None
    assert result["plan_id"] == "plan-fenced"
