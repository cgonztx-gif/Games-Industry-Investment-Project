"""
SEC EDGAR full-text search client for the Discovery worker's "new public
gaming company" trigger. Distinct from agents/workers/studio_intel/edgar_client.py,
which only pulls 8-K filings for tickers already in the studios table -- this
client searches ALL EDGAR filings for gaming-relevant keywords to surface
companies not yet tracked. Self-contained (not importing edgar_client.py) per
this repo's worker-packages-don't-cross-import convention (see the news/
patch_notes RSS parsers for precedent).

Verified live against the real endpoint while designing this:
https://efts.sec.gov/LATEST/search-index requires a declared User-Agent (SEC
returns 403 without one) and returns hits.hits[]._source with display_names
that embed the ticker, e.g.
"GameSquare Holdings, Inc.  (GAME)  (CIK 0001714562)" or, for multiple share
classes, "GameStop Corp.  (GME, GME-WT)  (CIK 0001326380)".

EDGAR rate limit: 10 req/sec; paced the same as edgar_client.py.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta

import requests

from agents.http_retry import request_with_retry

_HEADERS = {"User-Agent": "games-investment-platform cgonztx@gmail.com"}
_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_REQUEST_DELAY = 0.15

# Small, curated keyword set to keep this trigger low-noise -- a filing must
# actually mention gaming, not just come from a guessed SIC code.
_KEYWORDS = ['"video game"', '"interactive entertainment"']

# 8-K item codes that indicate an actual acquisition/change-of-control event,
# not a generic exhibit/press-release filing. Confirmed necessary via a live
# sample pulled while designing this: a plain "video game" text search on
# form 8-K was dominated by 7.01/9.01 earnings-release exhibits with no M&A
# content at all (e.g. GameStop's quarterly earnings release).
_ACQUISITION_ITEMS = {"2.01", "5.01"}

_DISPLAY_NAME_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<ticker>[A-Z.\-]+)(?:,.*)?\)\s+\(CIK")


class EdgarFulltextBlocked(Exception):
    """EDGAR full-text search failed or was blocked for this run."""


def _search(query: str, forms: str, start_date: str, end_date: str) -> list[dict]:
    time.sleep(_REQUEST_DELAY)
    resp = request_with_retry(
        requests.get,
        _SEARCH_URL,
        params={
            "q": query,
            "forms": forms,
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
        },
        headers=_HEADERS,
        timeout=15,
    )
    if resp.status_code != 200:
        raise EdgarFulltextBlocked(
            f"{resp.status_code} for EDGAR full-text search ({query!r}, forms={forms})"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise EdgarFulltextBlocked(f"non-JSON EDGAR full-text search response: {exc}") from exc
    return (data.get("hits") or {}).get("hits") or []


def _parse_hit(hit: dict) -> dict | None:
    """
    Returns None (rather than raising) for hits with no parseable ticker in
    display_names -- e.g. private filers with no public ticker. That's a
    feature, not a gap: no ticker means no resolvable public parent, so the
    hit correctly can't produce a proposal regardless.
    """
    source = hit.get("_source") or {}
    display_names = source.get("display_names") or []
    if not display_names:
        return None
    match = _DISPLAY_NAME_RE.match(display_names[0])
    if not match:
        return None

    cik = (source.get("ciks") or [None])[0]
    form = source.get("form")
    return {
        "company_name": match.group("name").strip(),
        "ticker": match.group("ticker").strip(),
        "cik": cik,
        "form": form,
        "items": [i.strip() for i in (source.get("items") or []) if i],
        "file_date": source.get("file_date"),
        "source_url": (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
            if cik
            else None
        ),
    }


def search_new_gaming_filings(
    known_tickers: set[str],
    days_back: int = 14,
    max_results: int = 5,
) -> list[dict]:
    """
    Scan recent S-1 (new IPO registrations) and 8-K (acquisition/change-of-
    control items only) filings for gaming-relevant keywords, excluding any
    ticker already in known_tickers. Returns up to max_results candidates,
    each {company_name, ticker, cik, form, items, file_date, source_url,
    trigger_signal}.

    Bounded and conservative by design -- this is a company-level "new public
    gaming company" signal, not a per-game trigger, so the Discovery worker
    treats it differently from the Steam/IGDB game-level candidates. Any
    failure (network, blocked, malformed response) degrades to an empty list
    rather than raising -- callers should treat this source as optional.
    """
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=days_back)).isoformat()

    candidates: dict[str, dict] = {}
    try:
        for keyword in _KEYWORDS:
            for hit in _search(keyword, "S-1", start_date, end_date):
                parsed = _parse_hit(hit)
                if parsed and parsed["ticker"] not in known_tickers:
                    candidates.setdefault(
                        parsed["ticker"], {**parsed, "trigger_signal": "edgar_new_ipo_filing"}
                    )

            for hit in _search(keyword, "8-K", start_date, end_date):
                parsed = _parse_hit(hit)
                if not parsed or parsed["ticker"] in known_tickers:
                    continue
                if not set(parsed["items"]) & _ACQUISITION_ITEMS:
                    continue
                candidates.setdefault(
                    parsed["ticker"], {**parsed, "trigger_signal": "edgar_new_acquisition_filing"}
                )
    except EdgarFulltextBlocked:
        return []

    return list(candidates.values())[:max_results]
