"""
Shared transient-error retry helper for worker API clients.

Every worker already degrades gracefully at the per-item level (one bad game/
studio/candidate is caught and logged, never crashes the whole run -- see
agents/workers/*/worker.py's per-item try/except loops). The gap this module
closes is narrower: most low-level HTTP call sites (RAWG, IGDB, SEC EDGAR,
Greenhouse/Lever/Ashby, blog/RSS fetches, ...) make a single one-shot request
and give up immediately on a connection blip or a transient 5xx/429, even
though the same request would likely succeed a moment later. Only
agents/workers/sentiment/reddit_source.py and agents/workers/news/gdelt_client.py
had their own retry-with-backoff loops before this module existed.

This lives at the agents/ top level (like agents/tracing.py), not inside any
agents/workers/<x>/ package, so every worker can use it without violating this
repo's worker-packages-don't-cross-import convention (see the news/patch_notes
RSS parser split, and gdelt_client.py's own from-scratch RateLimiter, for
precedent on why worker-specific code is deliberately duplicated instead).

Deliberately narrow: retries connection/timeout errors and 429/500/502/503/504
responses only. Any other status/exception (403, 404, a parse error, a
deliberate *Blocked signal exception, ...) is left completely alone -- this
module has no opinion on stale-cache fallback or per-item degradation, which
each caller already implements its own way.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

import requests

logger = logging.getLogger("http_retry")

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

T = TypeVar("T")


def request_with_retry(
    request_fn: Callable[..., requests.Response],
    *args,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    **kwargs,
) -> requests.Response:
    """
    Calls request_fn(*args, **kwargs) -- e.g. requests.get, session.post --
    retrying up to max_retries attempts total on a connection/timeout error or
    a 429/500/502/503/504 response, with exponential backoff (or the
    response's Retry-After header, if present, for a status-based retry).

    Returns the response as-is once a non-retryable status is seen, or once
    retries are exhausted (even if still a retryable status) -- callers keep
    calling .raise_for_status()/checking .status_code exactly as before this
    helper existed. Re-raises the underlying exception once retries are
    exhausted for the connection/timeout-error case.
    """
    last_exc: requests.exceptions.RequestException | None = None
    resp: requests.Response | None = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = request_fn(*args, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            logger.warning(
                "request error (attempt %d/%d): %s; retrying in %.1fs",
                attempt, max_retries, exc, delay,
            )
            time.sleep(delay)
            continue

        if resp.status_code not in _RETRYABLE_STATUSES or attempt == max_retries:
            return resp

        delay = float(resp.headers.get("Retry-After", backoff_base * (2 ** (attempt - 1))))
        logger.warning(
            "transient %s (attempt %d/%d); retrying in %.1fs",
            resp.status_code, attempt, max_retries, delay,
        )
        time.sleep(delay)

    if resp is not None:
        return resp
    raise last_exc  # pragma: no cover -- unreachable (loop always returns or raises above)


def retry_call(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """
    Calls fn() with no arguments (wrap a real call in a lambda/partial),
    retrying up to max_retries attempts total on any of `exceptions`, with
    exponential backoff. Re-raises the last exception once retries are
    exhausted. For clients that don't expose a raw requests call site (e.g.
    the yfinance wrapper, which makes its own HTTP calls internally).
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            logger.warning(
                "call error (attempt %d/%d): %s; retrying in %.1fs",
                attempt, max_retries, exc, delay,
            )
            time.sleep(delay)
    raise last_exc  # pragma: no cover -- unreachable (loop always returns or raises above)
