"""
Portfolio Manager - Phase 5 (Claude Agent SDK + MCP migration)

Reads the latest weekly briefing and produces this week's trade plan by running a
Claude Opus Agent SDK session that has a read-only Alpaca MCP tool available, then
writes the resulting plan to `trade_plans` / `trade_orders`.

What changed in the MCP migration (was: one raw `anthropic.Anthropic().messages.
create()` call with a pre-fetched account-state JSON blob stuffed into the user
message):

- The session now runs through the Claude Agent SDK (`claude_agent_sdk`). Claude
  pulls live account/position state itself by calling the read-only
  ``get_account_state`` MCP tool (see agents/portfolio/alpaca_mcp.py) instead of
  us pre-fetching it into a text blob. The tool is read-only, so there is no
  approval concern for this agent (every order it *writes* still lands 'pending').
- The agent's tool surface is pinned via ``allowed_tools`` to exactly that one
  read-only tool -- it cannot touch the guarded execution tools or anything else.
- The model is still locked to Opus (`claude-opus-4-8`) per CLAUDE.md's Agent
  Model Tiering table ("Synthesis Agent, Portfolio Manager" = Opus-class).

What is unchanged (the *contract* is preserved):

- The position-sizing-and-risk skill content is still the system prompt / single
  source of truth for sizing, limits, and the Output Contract.
- Claude still returns the same Output Contract JSON, parsed and written to
  `trade_plans` / `trade_orders` exactly as before. Every order lands with
  status='pending' (the trade_orders schema default) -- human approval is a
  separate step (scripts/review_trade_plans.py). This module must never write
  status='approved'.
- Graceful degradation is preserved: if the account tool cannot fetch state it
  returns an "UNAVAILABLE -- build conservatively" note (in alpaca_mcp.py) rather
  than failing, and the plan is still produced.

Failure-mode decision (unchanged from the pre-migration version): a failed
build_trade_plan() run means "no trade plan was produced this week" -- a fact the
caller genuinely needs. We return None rather than raising, matching the run()-style
convention used elsewhere (agents/synthesis/agent.py's run(), deep_dive's
run_deep_dive()). Every failure path prints a `[portfolio manager]` log line so the
miss is visible in run_weekly.py / GitHub Actions output.

Testability: the single injectable seam for the LLM call is ``run_agent_fn`` (it
replaces the old injectable ``client``). It receives the fully-built system/user
prompts, the MCP server dict, the allowed-tools allowlist, and the model, and
returns the assistant's final text (or None on refusal/error). The default
implementation drives the real Agent SDK session; tests inject a fake that returns
scripted text and records the prompts -- no live network/CLI calls. The account
tool's graceful-degradation text is unit-tested directly against
``alpaca_mcp.account_state_text`` (see tests).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path

from agents.portfolio.alpaca_mcp import (
    READONLY_SERVER_NAME,
    build_readonly_account_server,
)
from agents.portfolio.alpaca_trading_client import get_account_state
from database.db_client import (
    get_client,
    get_latest_weekly_briefing,
    write_trade_order,
    write_trade_plan,
)

_MODEL = "claude-opus-4-8"  # Opus-class per CLAUDE.md Agent Model Tiering table.

_MAX_TURNS = 6  # one account-tool round trip + the JSON answer, with headroom.

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

You have a read-only tool, get_account_state, that returns current Alpaca \
paper-trading cash, buying power, portfolio value, and open positions. Call it \
once before sizing positions so your plan reflects real current exposure. If it \
returns an "UNAVAILABLE" note, follow that note's instructions and build \
conservatively.

When you have what you need, respond with ONLY the JSON object described in the \
"Output Contract" section above -- no prose, no markdown fences, no commentary \
outside the JSON. Every order you propose must include all of the fields shown in \
that schema.
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

First call get_account_state to check current positions and cash, then return \
ONLY the JSON object matching the Output Contract schema, with "week_of" set to \
"{week_of}".
"""


def _load_skill_content() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def _strip_markdown_fence(raw: str) -> str:
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw, flags=re.DOTALL)
        raw = raw.strip()
    return raw


async def _default_run_agent_async(
    *, system_prompt: str, user_prompt: str, mcp_servers: dict, allowed_tools, model: str
) -> str | None:
    """
    Drive one real Claude Agent SDK session and return the assistant's final text.

    Imports the SDK lazily so this module (and its unit tests, which inject a fake
    runner) don't require the SDK/CLI transport to be importable. Returns None if
    the run is refused or ends in an error, so the caller treats it as "no plan"
    exactly like the pre-migration refusal/exception handling did.
    """
    from claude_agent_sdk import (  # local import: keeps the seam test-only
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=list(allowed_tools),
        # Restrict the agent to the single read-only Alpaca tool: no filesystem /
        # shell / web builtins. Auto-approve the allowlisted tool so the
        # non-interactive run never blocks on a permission prompt.
        permission_mode="bypassPermissions",
        setting_sources=None,  # ignore local Claude Code project settings
        max_turns=_MAX_TURNS,
    )

    text_parts: list[str] = []
    result_text: str | None = None
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            if message.stop_reason == "refusal" or message.error:
                return None
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                return None
            result_text = message.result

    final = result_text if result_text else "".join(text_parts)
    return final.strip() or None


def _default_run_agent(
    *, system_prompt: str, user_prompt: str, mcp_servers: dict, allowed_tools, model: str
) -> str | None:
    return asyncio.run(
        _default_run_agent_async(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            model=model,
        )
    )


def _parse_plan(raw: str | None) -> dict | None:
    """
    Parse the assistant's final text into an Output Contract dict, or None.

    Defensive parsing mirrors the pre-migration `_call_claude` (and absa_client.py /
    deep_dive.py): strip markdown fences, json.loads, require a dict with an
    ``orders`` list. Any failure returns None -- a malformed response means "no
    plan," not a crash.
    """
    if not raw:
        return None
    try:
        data = json.loads(_strip_markdown_fence(raw.strip()))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("orders"), list):
        return None
    return data


def _summarize_rationale(plan_json: dict) -> str:
    """
    Build a human-readable claude_rationale string from the orders and
    rejected_or_watch entries, rather than serializing the full JSON. The plan JSON
    itself isn't lost -- individual orders are written to trade_orders -- so this
    text field gives the approval CLI (scripts/review_trade_plans.py) a quick
    readable summary of Claude's reasoning, not a duplicate machine-readable payload.
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
    run_agent_fn=_default_run_agent,
    db=None,
    get_briefing_fn=get_latest_weekly_briefing,
    get_account_state_fn=get_account_state,
    write_trade_plan_fn=write_trade_plan,
    write_trade_order_fn=write_trade_order,
) -> dict | None:
    """
    Build and persist this week's trade plan.

    1. Fetch the latest weekly briefing. No briefing -> nothing to plan against ->
       return None.
    2. Build a read-only Alpaca MCP server (its ``get_account_state`` tool wraps
       ``get_account_state_fn`` and degrades to an "UNAVAILABLE -- build
       conservatively" note on any fetch failure).
    3. Run a Claude Opus Agent SDK session (via ``run_agent_fn``) with that one
       tool allowlisted; Claude calls it to read live state, then returns the
       position-sizing-and-risk Output Contract JSON.
    4. On a valid plan: write one trade_plans row (status stays at its schema
       default 'pending') and one trade_orders row per proposed order (status also
       stays 'pending' -- never auto-approved). Malformed individual orders
       (missing ticker, invalid action) are skipped and logged.
    5. Return a small summary dict, or None if no plan could be produced.

    All touch points -- the LLM session (``run_agent_fn``), the DB (``db``),
    briefing/account fetchers, and the two writers -- are injectable, matching the
    dependency-injection style used across this repo (e.g. agents/synthesis/
    agent.py's `_dispatch_deep_dives(deep_dive_fn=...)`). Tests inject a fake
    ``run_agent_fn`` and fake writers so no live network/API/CLI calls happen.
    """
    active_db = db if db is not None else get_client()

    briefing = get_briefing_fn(active_db)
    if briefing is None:
        print("[portfolio manager] no weekly briefing available; skipping trade plan")
        return None

    as_of = run_date or date.today().isoformat()

    server, allowed_tools = build_readonly_account_server(get_account_state_fn)

    system_prompt = _SYSTEM_TEMPLATE.format(skill_content=_load_skill_content())
    user_prompt = _USER_TEMPLATE.format(
        week_of=briefing["week_of"],
        as_of=as_of,
        briefing_text=briefing.get("briefing_text") or "",
        portfolio_update=json.dumps(briefing.get("portfolio_update") or {}, default=str),
        top_opportunities=json.dumps(briefing.get("top_opportunities") or [], default=str),
        risk_flags=json.dumps(briefing.get("risk_flags") or [], default=str),
        notable_events=json.dumps(briefing.get("notable_events") or {}, default=str),
    )

    try:
        raw = run_agent_fn(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            mcp_servers={READONLY_SERVER_NAME: server},
            allowed_tools=allowed_tools,
            model=_MODEL,
        )
    except Exception as exc:  # noqa: BLE001 -- a failed session means "no plan"
        print(f"[portfolio manager] Agent SDK session failed: {exc}; no trade plan produced")
        return None

    plan_json = _parse_plan(raw)
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
