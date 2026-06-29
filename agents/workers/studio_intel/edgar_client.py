"""
SEC EDGAR client for the Studio Intel worker.
Uses only public JSON APIs — no API key required.
EDGAR rate limit: 10 req/sec; callers should sleep ~0.12s between requests.
"""

import time
from datetime import date, timedelta

import requests

_HEADERS = {"User-Agent": "games-investment-platform cgonztx@gmail.com"}

# Module-level cache — loaded once per process
_cik_map: dict[str, int] = {}

# 8-K item prefix → (signal_type, severity)
_ITEM_MAP: dict[str, tuple[str, str]] = {
    "1.01": ("press_release", "low"),
    "2.01": ("acquisition", "high"),
    "2.05": ("layoffs", "high"),
    "2.06": ("press_release", "medium"),
    "5.01": ("acquisition", "high"),
    "5.02": ("exec_departure", "medium"),
    "8.01": ("press_release", "low"),
}


def load_cik_map() -> dict[str, int]:
    """Fetch EDGAR company_tickers.json and return {TICKER: cik_int}. Cached in-process."""
    global _cik_map
    if _cik_map:
        return _cik_map

    resp = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _cik_map = {v["ticker"].upper(): v["cik_str"] for v in data.values()}
    return _cik_map


def get_recent_8k_filings(cik: int, days_back: int = 60) -> list[dict]:
    """
    Return recent 8-K filings for a company, within the last `days_back` days.
    Each entry: {date, accession_number, items_raw, source_url}
    """
    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])
    items_list = recent.get("items", [])

    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    results = []

    for form, filing_date, accession, doc, items_raw in zip(
        forms, dates, accessions, documents, items_list
    ):
        if form != "8-K":
            continue
        if filing_date < cutoff:
            continue

        accession_clean = accession.replace("-", "")
        source_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/"
            f"{accession_clean}/{doc}"
        )
        results.append({
            "date": filing_date,
            "accession_number": accession,
            "items_raw": items_raw,
            "source_url": source_url,
        })

    return results


def classify_8k(items_raw: str) -> tuple[str, str]:
    """
    Map 8-K items string (e.g. '2.01, 5.02') to (signal_type, severity).
    When multiple items are present, highest-severity mapping wins.
    """
    _severity_rank = {"high": 3, "medium": 2, "low": 1}
    best_type = "press_release"
    best_severity = "low"

    for part in items_raw.split(","):
        key = part.strip()
        if key in _ITEM_MAP:
            sig_type, severity = _ITEM_MAP[key]
            if _severity_rank[severity] > _severity_rank[best_severity]:
                best_type = sig_type
                best_severity = severity

    return best_type, best_severity
