from __future__ import annotations

import json
import os
import re
import time

import requests

_REQUEST_DELAY = 0.25


def load_ats_board_map() -> dict[str, dict]:
    """
    Load optional hosted-ATS configuration.

    Expected env shape:
      STUDIO_ATS_BOARDS='{"Electronic Arts":{"greenhouse":"ea"},"Riot Games":{"lever":"riotgames"}}'

    Supported keys per studio: greenhouse, lever, ashby, careers_url.
    """
    raw = os.environ.get("STUDIO_ATS_BOARDS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _get_json(url: str, params: dict | None = None) -> dict | list:
    time.sleep(_REQUEST_DELAY)
    resp = requests.get(url, params=params or {}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_greenhouse_jobs(board_token: str) -> list[dict]:
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs",
        params={"content": "false"},
    )
    return [
        {
            "title": job.get("title", ""),
            "department": ((job.get("departments") or [{}])[0] or {}).get("name", ""),
            "location": ((job.get("location") or {}).get("name") or ""),
            "url": job.get("absolute_url"),
        }
        for job in (data.get("jobs") or [])
    ]


def fetch_lever_jobs(company: str) -> list[dict]:
    data = _get_json(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"})
    return [
        {
            "title": job.get("text", ""),
            "department": job.get("team", ""),
            "location": job.get("categories", {}).get("location", ""),
            "url": job.get("hostedUrl"),
        }
        for job in (data or [])
    ]


def fetch_ashby_jobs(board_token: str) -> list[dict]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board_token}")
    return [
        {
            "title": job.get("title", ""),
            "department": job.get("department", ""),
            "location": (job.get("location") or {}).get("name", ""),
            "url": job.get("jobUrl"),
        }
        for job in (data.get("jobs") or [])
    ]


def fetch_playwright_careers_page(careers_url: str) -> list[dict]:
    """
    Low-volume fallback for studios without hosted ATS boards.

    The fallback intentionally returns lightweight title-like snippets only; use
    hosted ATS APIs whenever possible.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(careers_url, wait_until="domcontentloaded", timeout=20000)
        text = page.locator("body").inner_text(timeout=10000)
        browser.close()

    jobs: list[dict] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if 8 <= len(cleaned) <= 120 and _looks_like_role(cleaned):
            jobs.append({"title": cleaned, "department": "", "location": "", "url": careers_url})
    return jobs[:50]


def fetch_configured_jobs(config: dict) -> list[dict]:
    if config.get("greenhouse"):
        return fetch_greenhouse_jobs(config["greenhouse"])
    if config.get("lever"):
        return fetch_lever_jobs(config["lever"])
    if config.get("ashby"):
        return fetch_ashby_jobs(config["ashby"])
    if config.get("careers_url"):
        return fetch_playwright_careers_page(config["careers_url"])
    return []


def _looks_like_role(title: str) -> bool:
    blob = title.lower()
    return any(
        term in blob
        for term in (
            "engineer",
            "designer",
            "producer",
            "artist",
            "qa",
            "monetization",
            "live",
            "backend",
            "gameplay",
            "data",
        )
    )


def summarize_hiring_signal(jobs: list[dict]) -> tuple[str, str] | None:
    if not jobs:
        return None

    titles = " ".join(job.get("title", "") for job in jobs).lower()
    live_roles = len(re.findall(r"\b(live|online|backend|server|monetization|economy)\b", titles))
    qa_roles = len(re.findall(r"\bqa|quality\b", titles))
    total = len(jobs)

    if live_roles >= 3:
        return "hiring_surge", f"{total} open roles; {live_roles} live-service/online/economy roles detected"
    if qa_roles >= 5:
        return "hiring_surge", f"{total} open roles; {qa_roles} QA roles may indicate launch or content-readiness push"
    if total >= 20:
        return "hiring_surge", f"{total} open roles detected on configured ATS board"
    return None
