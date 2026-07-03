"""
Portfolio Manager - Phase 5

Reads the latest weekly briefing plus current Alpaca paper-trading account
state, applies the position-sizing-and-risk methodology (single source of
truth: agents/skills/position-sizing-and-risk/SKILL.md) via one Claude Opus
call, and writes the resulting trade plan to `trade_plans` / `trade_orders`.

This is the Opus-class reasoning step referenced in CLAUDE.md's Agent Model
Tiering table ("Synthesis Agent, Portfolio Manager"). Every order this module
writes lands with status='pending' (the trade_orders schema default) -- human
approval is a separate, not-yet-built step (see tasks.md Phase 5: "Build
minimal trade-plan approval UI or CLI flow before enabling execution"). This
module must never write status='approved'.

Failure-mode decision: unlike agents/synthesis/deep_dive.py (optional
enrichment, where None just means "no extra color this week" and synthesis
carries on regardless), a failed build_trade_plan() run means "no trade plan
was produced this week" -- a fact the caller genuinely needs to know. We still
return None rather than raising, to match the run()-style convention used
elsewhere in this repo (agents/synthesis/agent.py's run(), deep_dive's
run_deep_dive()) where callers check for a falsy/None result rather than
catching exceptions. Every failure path prints a `[portfolio manager]` log
line so the miss is visible in run_weekly.py / GitHub Actions output even
though the return value alone doesn't carry the reason.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import anthropic

from agents.portfolio.alpaca_trading_client import get_account_state
from database.db_client import (
    get_client,
    get_latest_weekly_briefing,
    write_trade_order,
    write_trade_plan,
)

_MODEL = "claude-opus-4-8"
_client = anthropic.Anthropic()

_MAX_TOKENS = 8000

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "agents"
    / "skills"
    / "position-sizing-and-risk"
    / "SKILL.md"
)

_VALID_ACTIONS = {"buy", "sell", "hold"}

_SYSTEM_TEMPLATE = """\
You are the Portfolio Manager agent for a games-industry investment intelligence \
platform. Apply the following methodology exactly -- it is the single source of \
truth for conviction-tier sizing, position/concentration limits, cash buffer \
rules, entry discipline, stop-loss/thesis-invalidation rules, and the required \
benchmark-relative framing. Do not invent rules or fields beyond what it \
documents.

{skill_content}

Respond with ONLY the JSON object described in the "Output Contract" section \
above -- no prose, no markdown fences, no commentary outside the JSON. Every \
order you propose must include all of the fields shown in that schema.
"""

_USER_TEMPLATE = """\
Build this week's trade plan. Plan as-of date: {as_of}. Use week_of "{week_of}" \
in your response (the same week as the briefing below).

## Weekly briefing (week_of {week_of})

briefing_text: {briefing_text}

portfolio_update: {portfolio_update}

top_opportunities: {top_opportunities}

risk_flags: {risk_flags}

notable_events: {notable_events}

## Current Alpaca paper-trading account state

{account_state_block}

Return ONLY the JSON object matching the Output Contract schema, with \
"week_of" set to "{week_of}".
"""

_ACCOUNT_UNAVAILABLE_NOTE = (
    "UNAVAILABLE -- Alpaca account state could not be fetched this run "
    "({reason}). Current positions, cash, and buying power cannot be "
    "verified. Build this plan conservatively: do not assume any existing "
    "position or cash balance, keep target weights modest, favor 'watch' "
    "over 'buy' for anything whose sizing depends on unverifiable current "
    "exposure, and note this limitation in the relevant rationale fields."
)


def _load_skill_content() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def _strip_markdown_fence(raw: str) -> str:
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw, flags=re.DOTALL)
        raw = raw.strip()
    return raw


def _call_claude(client, system_prompt: str, user_message: str) -> dict | None:
    """
    Single Opus call requesting the position-sizing-and-risk Output Contract
    JSON. Defensive parsing mirrors absa_client.py / deep_dive.py: strip
    markdown fences, json.loads, guard the refusal stop reason, and swallow
    any other exception -- all return None rather than propagating, since a
    malformed or refused response here means "no plan," not a crash.
    """
    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": user_message}],
        )

        if msg.stop_reason == "refusal":
            return None

        text_parts = [
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        ]
        if not text_parts:
            return None

        raw = _strip_markdown_fence("".join(text_parts).strip())
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("orders"), list):
            return None
        return data
    except Exception:
        return None


def _summarize_rationale(plan_json: dict) -> str:
    """
    Build a human-readable claude_rationale string from the orders and
    rejected_or_watch entries, rather than serializing the full JSON. The
    plan JSON itself isn't lost -- individual orders are written to
    trade_orders -- so this text field is meant to give a future approval
    UI/CLI a quick readable summary of Claude's reasoning, not a duplicate
    machine-readable payload.
    """
    parts = [
        f"Portfolio risk posture: {plan_json.get('portfolio_risk_posture', 'unspecified')}."
    ]
    for order in plan_json.get("orders") or []:
        ticker = order.get("ticker", "?")
        action = order.get("action", "?")
        tier = order.get("conviction_tier", "?")
        rationale = order.get("rationale", "")
        parts.append(f"{ticker} ({action}, {tier}): {rationale}")
    for item in plan_json.get("rejected_or_watch") or []:
        ticker = item.get("ticker", "?")
        reason = item.get("reason", "")
        parts.append(f"Watch/reject {ticker}: {reason}")
    return " ".join(parts)


def build_trade_plan(
    run_date: str | None = None,
    client=None,
    db=None,
    get_briefing_fn=get_latest_weekly_briefing,
    get_account_state_fn=get_account_state,
    write_trade_plan_fn=write_trade_plan,
    write_trade_order_fn=write_trade_order,
) -> dict | None:
    """
    Build and persist this week's trade plan.

    1. Fetch the latest weekly briefing. No briefing -> nothing to plan
       against -> return None.
    2. Attempt to fetch current Alpaca account state; on any failure
       (missing credentials, network error, etc.) proceed with a clear
       "unavailable" note in the prompt instead of failing the whole run.
    3. Ask Claude Opus for the position-sizing-and-risk Output Contract JSON.
    4. On a valid plan: write one trade_plans row (status stays at its
       schema default 'pending') and one trade_orders row per proposed order
       (status also stays 'pending' -- never auto-approved). Malformed
       individual orders (missing ticker, invalid action) are skipped and
       logged rather than failing the whole write.
    5. Return a small summary dict, or None if no plan could be produced.

    All five DB/Alpaca/Anthropic touch points are injectable for testing,
    matching the dependency-injection style used by
    agents/synthesis/agent.py's `_dispatch_deep_dives(deep_dive_fn=...)`.
    """
    active_client = client or _client
    active_db = db or get_client()

    briefing = get_briefing_fn(active_db)
    if briefing is None:
        print("[portfolio manager] no weekly briefing available; skipping trade plan")
        return None

    as_of = run_date or date.today().isoformat()

    try:
        account_state = get_account_state_fn()
        account_state_block = json.dumps(account_state, indent=2, default=str)
    except Exception as exc:
        print(f"[portfolio manager] Alpaca account state unavailable: {exc}")
        account_state_block = _ACCOUNT_UNAVAILABLE_NOTE.format(reason=exc)

    system_prompt = _SYSTEM_TEMPLATE.format(skill_content=_load_skill_content())
    user_message = _USER_TEMPLATE.format(
        week_of=briefing["week_of"],
        as_of=as_of,
        briefing_text=briefing.get("briefing_text") or "",
        portfolio_update=json.dumps(briefing.get("portfolio_update") or {}, default=str),
        top_opportunities=json.dumps(briefing.get("top_opportunities") or [], default=str),
        risk_flags=json.dumps(briefing.get("risk_flags") or [], default=str),
        notable_events=json.dumps(briefing.get("notable_events") or {}, default=str),
        account_state_block=account_state_block,
    )

    plan_json = _call_claude(active_client, system_prompt, user_message)
    if plan_json is None:
        print(
            "[portfolio manager] Claude call failed, was refused, or returned "
            "unparseable output; no trade plan produced this week"
        )
        return None

    watch = plan_json.get("rejected_or_watch") or []
    rationale = _summarize_rationale(plan_json)

    plan_row = {
        "week_of": briefing["week_of"],
        "briefing_id": briefing.get("id"),
        "claude_rationale": rationale,
    }
    plan_id = write_trade_plan_fn(active_db, plan_row)

    written = 0
    for order in plan_json.get("orders") or []:
        ticker = order.get("ticker")
        action = order.get("action")
        if not ticker or action not in _VALID_ACTIONS:
            print(
                f"[portfolio manager] skipping malformed order "
                f"(ticker={ticker!r}, action={action!r})"
            )
            continue
        order_row = {
            "plan_id": plan_id,
            "ticker": ticker,
            "action": action,
            "size_usd": order.get("size_usd"),
        }
        write_trade_order_fn(active_db, order_row)
        written += 1

    print(
        f"[portfolio manager] trade plan {plan_id} written for week_of "
        f"{briefing['week_of']}: {written} order(s), {len(watch)} watch item(s)"
    )

    return {
        "week_of": briefing["week_of"],
        "plan_id": plan_id,
        "order_count": written,
        "watch_count": len(watch),
    }
