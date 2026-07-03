"""
Email delivery for the weekly briefing - Phase 4 synthesis agent.

Optional enrichment dispatched by agents/synthesis/agent.py right after the
briefing dict is written to Supabase (`write_weekly_briefing`). Sends a plain
HTML summary of the briefing via Resend's REST API using `requests` directly
-- no `resend` SDK dependency, consistent with every other external-API
adapter in this codebase (steam_news_client.py, blog_client.py, etc.).

Request shape confirmed against Resend's API reference
(https://resend.com/docs/api-reference/emails/send-email):
  POST https://api.resend.com/emails
  Headers: Authorization: Bearer <RESEND_API_KEY>, Content-Type: application/json
  Body: {"from": "...", "to": ["..."], "subject": "...", "html": "..."}
  Response on success: {"id": "<email-uuid>"} (not otherwise relied upon here).

Fully opt-in, same pattern as agents/tracing.py's configure_tracing() and
agents/synthesis/deep_dive.py's run_deep_dive(): with no RESEND_API_KEY or
BRIEFING_EMAIL_TO configured, send_briefing_email() is a no-op that returns
False without making a network call. Any failure in the HTTP call itself
(network error, timeout, non-2xx response) is caught, logged, and also
returns False -- it must never raise or block the caller.
"""

from __future__ import annotations

import os

import requests

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 15

# Resend's sandbox sender for accounts without a verified custom domain (see
# https://resend.com/docs/api-reference/emails/send-email -- "onboarding@
# resend.dev" is their documented test address; mail sent from it can only
# reach the account owner's own verified address until a custom domain is
# verified). Real deployments should set BRIEFING_EMAIL_FROM to a verified
# sender once a domain is configured in Resend.
_DEFAULT_FROM = "Games Industry Intel <onboarding@resend.dev>"


def _bullet_list(items: list) -> str:
    if not items:
        return "<p><em>None this week.</em></p>"
    rows = "".join(f"<li>{_escape(str(item))}</li>" for item in items)
    return f"<ul>{rows}</ul>"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_html(briefing: dict) -> str:
    week_of = briefing.get("week_of", "")
    briefing_text = briefing.get("briefing_text", "")
    top_opportunities = briefing.get("top_opportunities") or []
    risk_flags = briefing.get("risk_flags") or []
    notable_events = briefing.get("notable_events") or {}

    if isinstance(notable_events, dict):
        notable_items = [f"{key}: {value}" for key, value in notable_events.items()]
    elif isinstance(notable_events, list):
        notable_items = notable_events
    else:
        notable_items = [str(notable_events)] if notable_events else []

    return (
        f"<h2>Weekly Briefing - {_escape(str(week_of))}</h2>"
        f"<p>{_escape(str(briefing_text))}</p>"
        "<h3>Top Opportunities</h3>"
        f"{_bullet_list(top_opportunities)}"
        "<h3>Risk Flags</h3>"
        f"{_bullet_list(risk_flags)}"
        "<h3>Notable Events</h3>"
        f"{_bullet_list(notable_items)}"
    )


def send_briefing_email(briefing: dict) -> bool:
    """
    Send the weekly briefing as an HTML email via Resend.

    Returns True only on a successful (2xx) response from Resend. Returns
    False -- and never raises -- whenever:
      - RESEND_API_KEY or BRIEFING_EMAIL_TO is unset (no network call made)
      - the HTTP request raises (network error, timeout, etc.)
      - Resend responds with a non-2xx status code
    """
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    to_raw = os.environ.get("BRIEFING_EMAIL_TO", "").strip()

    if not api_key or not to_raw:
        print(
            "[email_delivery] skipped: RESEND_API_KEY/BRIEFING_EMAIL_TO not configured"
        )
        return False

    to_addresses = [addr.strip() for addr in to_raw.split(",") if addr.strip()]
    if not to_addresses:
        print(
            "[email_delivery] skipped: RESEND_API_KEY/BRIEFING_EMAIL_TO not configured"
        )
        return False

    from_address = os.environ.get("BRIEFING_EMAIL_FROM", "").strip() or _DEFAULT_FROM
    week_of = briefing.get("week_of", "")

    payload = {
        "from": from_address,
        "to": to_addresses,
        "subject": f"Games Industry Weekly Briefing - {week_of}",
        "html": _build_html(briefing),
    }

    try:
        response = requests.post(
            _RESEND_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        if 200 <= response.status_code < 300:
            return True
        print(
            f"[email_delivery] failed: Resend returned status {response.status_code}: "
            f"{response.text[:500]}"
        )
        return False
    except Exception as exc:
        print(f"[email_delivery] failed: {exc}")
        return False
