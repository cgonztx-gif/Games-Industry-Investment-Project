"""
Unit tests for agents/http_retry.py.

No live network calls: request_fn/fn are plain fakes. time.sleep is
monkeypatched to a no-op -- since every worker module does `import time` (the
same singleton module object), patching time.sleep here also silences retry
delays in every other client module's tests that don't separately patch it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests

from agents.http_retry import request_with_retry, retry_call


class _FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


# ---------------------------------------------------------------------------
# request_with_retry
# ---------------------------------------------------------------------------

def test_returns_immediately_on_200(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return _FakeResponse(200)

    resp = request_with_retry(fake_get, "http://example.com")

    assert resp.status_code == 200
    assert len(calls) == 1


def test_non_retryable_status_returned_immediately(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return _FakeResponse(404)

    resp = request_with_retry(fake_get, "http://example.com")

    assert resp.status_code == 404
    assert len(calls) == 1


def test_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    responses = iter([_FakeResponse(503), _FakeResponse(503), _FakeResponse(200)])

    def fake_get(*a, **kw):
        return next(responses)

    resp = request_with_retry(fake_get, "http://example.com", max_retries=3)

    assert resp.status_code == 200


def test_exhausts_retries_and_returns_final_retryable_response(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)

    def fake_get(*a, **kw):
        return _FakeResponse(503)

    resp = request_with_retry(fake_get, "http://example.com", max_retries=3)

    assert resp.status_code == 503


def test_respects_retry_after_header(monkeypatch):
    sleeps = []
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda s: sleeps.append(s))
    responses = iter([_FakeResponse(429, headers={"Retry-After": "7"}), _FakeResponse(200)])

    def fake_get(*a, **kw):
        return next(responses)

    resp = request_with_retry(fake_get, "http://example.com", max_retries=3)

    assert resp.status_code == 200
    assert sleeps == [7.0]


def test_retries_on_connection_error_then_succeeds(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    responses = iter([requests.exceptions.ConnectionError("boom"), _FakeResponse(200)])

    def fake_get(*a, **kw):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    resp = request_with_retry(fake_get, "http://example.com", max_retries=3)

    assert resp.status_code == 200


def test_reraises_connection_error_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)

    def fake_get(*a, **kw):
        raise requests.exceptions.ConnectionError("boom")

    with pytest.raises(requests.exceptions.ConnectionError):
        request_with_retry(fake_get, "http://example.com", max_retries=3)


def test_passes_through_args_and_kwargs(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _FakeResponse(200)

    request_with_retry(fake_get, "http://example.com", params={"q": "1"}, timeout=15)

    assert captured == {"url": "http://example.com", "params": {"q": "1"}, "timeout": 15}


def test_non_request_exception_propagates_without_retry(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        raise ValueError("not a requests exception")

    with pytest.raises(ValueError):
        request_with_retry(fake_get, "http://example.com", max_retries=3)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# retry_call
# ---------------------------------------------------------------------------

def test_retry_call_returns_on_first_success(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert retry_call(fn) == "ok"
    assert len(calls) == 1


def test_retry_call_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    attempts = iter([ValueError("first"), ValueError("second"), "ok"])

    def fn():
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    assert retry_call(fn, max_retries=3) == "ok"


def test_retry_call_reraises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)

    def fn():
        raise ValueError("always fails")

    with pytest.raises(ValueError):
        retry_call(fn, max_retries=3)


def test_retry_call_only_retries_specified_exceptions(monkeypatch):
    monkeypatch.setattr("agents.http_retry.time.sleep", lambda _: None)
    calls = []

    def fn():
        calls.append(1)
        raise TypeError("not retryable per this call's config")

    with pytest.raises(TypeError):
        retry_call(fn, max_retries=3, exceptions=(ValueError,))
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Retry-After parsing (regression: HTTP-date form crashed the retry loop)
# ---------------------------------------------------------------------------

def test_http_date_retry_after_does_not_crash_and_still_retries(monkeypatch):
    """Retry-After is legally either delta-seconds or an HTTP-date. float() on
    the date form used to raise ValueError straight through the retry loop --
    an exception type no caller expects from a retryable 429."""
    sleeps = []
    monkeypatch.setattr("agents.http_retry.time.sleep", sleeps.append)
    responses = [
        _FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _FakeResponse(200),
    ]

    resp = request_with_retry(lambda *a, **kw: responses.pop(0), "http://example.com")

    assert resp.status_code == 200
    # Fell back to exponential backoff instead of crashing.
    assert sleeps == [1.0]


def test_huge_retry_after_is_capped(monkeypatch):
    """A server demanding an hours-long wait would stall the whole per-item
    worker loop; the honored delay is clamped to the module ceiling."""
    from agents.http_retry import _MAX_RETRY_AFTER_SECONDS

    sleeps = []
    monkeypatch.setattr("agents.http_retry.time.sleep", sleeps.append)
    responses = [
        _FakeResponse(429, headers={"Retry-After": "86400"}),
        _FakeResponse(200),
    ]

    resp = request_with_retry(lambda *a, **kw: responses.pop(0), "http://example.com")

    assert resp.status_code == 200
    assert sleeps == [_MAX_RETRY_AFTER_SECONDS]


def test_numeric_retry_after_is_honored(monkeypatch):
    sleeps = []
    monkeypatch.setattr("agents.http_retry.time.sleep", sleeps.append)
    responses = [
        _FakeResponse(429, headers={"Retry-After": "7"}),
        _FakeResponse(200),
    ]

    resp = request_with_retry(lambda *a, **kw: responses.pop(0), "http://example.com")

    assert resp.status_code == 200
    assert sleeps == [7.0]
