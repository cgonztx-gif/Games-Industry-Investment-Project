"""
Unit tests for agents/portfolio/manager.py.

No live network and no real Anthropic/Supabase/Alpaca calls: build_trade_plan()
takes injectable `client`, `db`, and function overrides for
get_latest_weekly_briefing / get_account_state / write_trade_plan /
write_trade_order, so every test uses fakes with scripted behavior (same
dependency-injection style as tests/test_deep_dive.py and
agents/synthesis/agent.py's `_dispatch_deep_dives(deep_dive_fn=...)`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.portfolio.manager import build_trade_plan

_FAKE_DB = "fake-db-sentinel"  # never touched directly; only passed through to fakes


class FakeAnthropicClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.messages = SimpleNamespace(create=self._create)
        self.last_kwargs = None

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc:
            raise self._exc
        return self._response


def _text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


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
    client = FakeAnthropicClient(response=_text_response(_VALID_PLAN_JSON))

    result = build_trade_plan(
        client=client,
        db=_FAKE_DB,
        get_briefing_fn=lambda db: None,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None
    assert writers.plan_calls == []
    assert writers.order_calls == []


def test_valid_plan_writes_plan_and_orders():
    writers = _FakeDBWriters(plan_id="plan-abc")
    client = FakeAnthropicClient(response=_text_response(_VALID_PLAN_JSON))

    result = build_trade_plan(
        run_date="2026-06-29",
        client=client,
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

    # Claude was asked with the skill content + briefing content in the prompt
    sent_system = client.last_kwargs["system"]
    assert "Output Contract" in sent_system
    sent_user = client.last_kwargs["messages"][0]["content"]
    assert "2026-06-29" in sent_user


def test_account_state_unavailable_still_produces_plan():
    writers = _FakeDBWriters(plan_id="plan-xyz")
    client = FakeAnthropicClient(response=_text_response(_VALID_PLAN_JSON))

    def _account_state_fails():
        raise RuntimeError("no credentials")

    result = build_trade_plan(
        client=client,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_fails,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is not None
    assert result["plan_id"] == "plan-xyz"
    sent_user = client.last_kwargs["messages"][0]["content"]
    assert "UNAVAILABLE" in sent_user
    assert "no credentials" in sent_user


def test_refusal_stop_reason_returns_none():
    writers = _FakeDBWriters()
    client = FakeAnthropicClient(response=_text_response("", stop_reason="refusal"))

    result = build_trade_plan(
        client=client,
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
    client = FakeAnthropicClient(response=_text_response("not valid json"))

    result = build_trade_plan(
        client=client,
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
    client = FakeAnthropicClient(response=_text_response('{"portfolio_risk_posture": "balanced"}'))

    result = build_trade_plan(
        client=client,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is None


def test_client_exception_returns_none():
    writers = _FakeDBWriters()
    client = FakeAnthropicClient(exc=RuntimeError("network error"))

    result = build_trade_plan(
        client=client,
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
    client = FakeAnthropicClient(response=_text_response(fenced))

    result = build_trade_plan(
        client=client,
        db=_FAKE_DB,
        get_briefing_fn=_get_briefing_ok,
        get_account_state_fn=_account_state_ok,
        write_trade_plan_fn=writers.write_trade_plan,
        write_trade_order_fn=writers.write_trade_order,
    )

    assert result is not None
    assert result["plan_id"] == "plan-fenced"
