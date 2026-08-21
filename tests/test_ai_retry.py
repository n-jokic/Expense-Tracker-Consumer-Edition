"""
AI-03 regression tests — transient provider outages never lose answers.

429/500/502/503/504 get at most two bounded retries honoring a capped
Retry-After; permanent 4xx never retry; persistent 503 reports temporary
unavailability; a deterministic tool result still produces a deterministic
answer when composition fails.
"""

import requests
import pytest

import llm


class FakeResp:
    def __init__(self, status, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err


_OK_PAYLOAD = {"choices": [{"message": {"content": " all good "}}]}


@pytest.fixture()
def no_key_needed(monkeypatch):
    monkeypatch.setattr(llm, "decrypt_str", lambda s: "test-key")


@pytest.fixture()
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(llm, "_backoff_sleep", lambda s: recorded.append(s))
    return recorded


SETTINGS = {"ai_api_base": "https://api.example.test", "ai_api_model": "m"}


def test_transient_503_then_success_recovers(no_key_needed, sleeps, monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return FakeResp(503)
        return FakeResp(200, payload=_OK_PAYLOAD)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    res = llm._api_chat(SETTINGS, "sys", "user", 64)
    assert res.text == "all good"
    assert res.diagnostic == ""
    assert len(calls) == 2 and len(sleeps) == 1


def test_persistent_503_reports_temporary_unavailability(no_key_needed, sleeps, monkeypatch):
    attempts = []

    def fake_post(url, headers=None, json=None, timeout=None):
        attempts.append(1)
        return FakeResp(503)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    res = llm._api_chat(SETTINGS, "sys", "user", 64)
    assert res.text is None
    assert len(attempts) == 3              # initial + at most TWO retries
    assert len(sleeps) == 2
    assert "temporarily unavailable" in res.diagnostic
    assert "503" in res.diagnostic


def test_retry_after_header_honored_but_capped(no_key_needed, sleeps, monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResp(429, headers={"Retry-After": "120"})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._api_chat(SETTINGS, "sys", "user", 64)
    assert sleeps and max(sleeps) <= llm._RETRY_AFTER_CAP_S


def test_permanent_401_never_retries(no_key_needed, sleeps, monkeypatch):
    attempts = []

    def fake_post(url, headers=None, json=None, timeout=None):
        attempts.append(1)
        return FakeResp(401)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    res = llm._api_chat(SETTINGS, "sys", "user", 64)
    assert len(attempts) == 1              # no retry on permanent 4xx
    assert sleeps == []
    assert "API key" in res.diagnostic


def test_timeout_retries_then_succeeds(no_key_needed, sleeps, monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.Timeout("timed out")
        return FakeResp(200, payload=_OK_PAYLOAD)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    res = llm._api_chat(SETTINGS, "sys", "user", 64)
    assert res.text == "all good"
    assert len(calls) == 3 and len(sleeps) == 2


def test_persistent_timeout_diagnoses_unreachable(no_key_needed, sleeps, monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    res = llm._api_chat(SETTINGS, "sys", "user", 64)
    assert res.text is None
    assert "could not be reached" in res.diagnostic


def test_sanitized_payload_resent_identically_on_retry(no_key_needed, sleeps, monkeypatch):
    """AI-01 boundary holds across retries: same sanitized body every time."""
    bodies = []

    def fake_post(url, headers=None, json=None, timeout=None):
        bodies.append(json)
        if len(bodies) == 1:
            return FakeResp(500)
        return FakeResp(200, payload=_OK_PAYLOAD)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    secret_user = "my token is ghp_" + "a" * 36 + " — spending?"
    llm._api_chat(SETTINGS, "sys", secret_user, 64)
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]          # identical (sanitized) payloads
    assert "ghp_" + "a" * 36 not in bodies[0]["messages"][1]["content"]


# ── Deterministic fallback when composition fails ────────────────────────────

def test_deterministic_answer_survives_composer_failure():
    from ai.orchestrator import _compose_answer
    from ai.schemas import AdvisorToolCall

    class _ExplodingProvider:
        def generate(self, request):
            raise RuntimeError("provider down")

    import ai.orchestrator as orch
    orig = orch._get_provider
    orch._get_provider = lambda settings: _ExplodingProvider()
    try:
        tc = AdvisorToolCall(tool="aggregate_spending", arguments={"year": 2026, "month": 2},
                             result={"total_eur": 123.45, "_provenance": {"row_count": 7,
                                     "calculation": "aggregate_spending"}},
                             error=None)
        answer, diag = _compose_answer("spending?", [tc], {})
    finally:
        orch._get_provider = orig
    assert answer is not None and "123.45" in answer
    assert diag == "deterministic fallback"
