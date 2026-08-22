"""#22 — Gemini provider: dispatch, endpoint shape, JSON mode, retries,
key-rejection diagnostics, and the one-time ai_api_kind backfill."""
import pytest

import db
from ai.providers.base import GenerationRequest
from auth import hash_password
from db import (create_user, delete_user_account, get_engine,
                get_settings, get_user_by_username, init_db,
                save_settings, username_exists)

U = "gem_user"
E = "gem@example.com"


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Gem Tester")
    yield uid
    delete_user_account(uid)


def _req(**kw):
    return GenerationRequest(system="sys", user="usr", max_tokens=64, **kw)


def test_orchestrator_dispatches_gemini(monkeypatch):
    import ai.orchestrator as orch
    import llm
    monkeypatch.setattr(llm, "resolve_provider", lambda s: "api")
    from ai.providers.gemini import GeminiProvider
    from ai.providers.openai_compatible import OpenAICompatibleProvider

    p = orch._get_provider({"ai_provider": "api", "ai_api_kind": "gemini"})
    assert isinstance(p, GeminiProvider)
    # kind wins; a foreign base URL must NOT flip the family anymore
    p2 = orch._get_provider({"ai_provider": "api",
                             "ai_api_kind": "openai_compatible",
                             "ai_api_base": "https://x.anthropic.com"})
    assert isinstance(p2, OpenAICompatibleProvider)


def _capture_post(monkeypatch, responses):
    """Replace requests.post inside the gemini module with a scripted fake."""
    import ai.providers.gemini as gem

    calls = []

    class FakeResp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload or {}
            self.headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.exceptions.HTTPError(
                    f"{self.status_code}", response=self)

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        status, payload = responses[min(len(calls) - 1, len(responses) - 1)]
        return FakeResp(status, payload)

    monkeypatch.setattr(gem.requests, "post", fake_post)
    monkeypatch.setattr(gem, "_backoff_sleep", lambda s: None)
    return calls


def test_generate_success_parses_candidates(monkeypatch):
    from ai.providers.gemini import GeminiProvider
    calls = _capture_post(
        monkeypatch,
        [(200, {"candidates": [{"content": {"parts": [
            {"text": "hello "}, {"text": "world"}]}}]})])
    prov = GeminiProvider({"ai_api_key_enc": __import__(
        "crypto").encrypt_str("k123")})
    res = prov.generate(_req())
    assert res.text == "hello world"
    url = calls[0]["url"]
    assert ":generateContent" in url and "gemini-2.0-flash" in url
    assert calls[0]["headers"]["x-goog-api-key"] == "k123"


def test_json_mode_sets_response_mime(monkeypatch):
    from ai.providers.gemini import GeminiProvider
    calls = _capture_post(
        monkeypatch,
        [(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})])
    prov = GeminiProvider({"ai_api_key_enc": __import__(
        "crypto").encrypt_str("k")})
    prov.generate(_req(wants_json=True))
    assert (calls[0]["json"]["generationConfig"]["responseMimeType"]
            == "application/json")


def test_transient_retry_then_success(monkeypatch):
    from ai.providers.gemini import GeminiProvider
    _capture_post(
        monkeypatch,
        [(429, {}), (500, {}),
         (200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})])
    prov = GeminiProvider({"ai_api_key_enc": __import__(
        "crypto").encrypt_str("k")})
    res = prov.generate(_req())
    assert res.text == "ok"


def test_key_rejected_diagnostic(monkeypatch):
    from ai.providers.gemini import GeminiProvider
    _capture_post(monkeypatch, [(403, {})])
    prov = GeminiProvider({"ai_api_key_enc": __import__(
        "crypto").encrypt_str("bad")})
    res = prov.generate(_req())
    assert res.text is None and "rejected" in (res.diagnostic or "")


def test_offline_degrades_to_diagnostic(monkeypatch):
    import requests as _rq
    import ai.providers.gemini as gem
    attempts = {"n": 0}

    def conn_fail(**kw):
        attempts["n"] += 1
        raise _rq.exceptions.ConnectionError("down")

    monkeypatch.setattr(gem.requests, "post",
                        lambda *a, **kw: conn_fail(**kw))
    monkeypatch.setattr(gem, "_backoff_sleep", lambda s: None)
    from ai.providers.gemini import GeminiProvider
    prov = GeminiProvider({"ai_api_key_enc": __import__(
        "crypto").encrypt_str("k")})
    res = prov.generate(_req())
    assert res.text is None and attempts["n"] == gem._MAX_ATTEMPTS
    assert "connection" in (res.diagnostic or "").lower()


def test_ai_api_kind_backfill_from_legacy_url(user):
    from sqlalchemy import text
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE user_settings SET ai_api_kind = NULL, "
            "ai_api_base = 'https://proxy.internal/anthropic' "
            "WHERE user_id = :u"), {"u": int(user)})
    db._derive_ai_api_kind(eng)
    assert get_settings(user)["ai_api_kind"] == "anthropic"
    # idempotent: a second run keeps values and never invents kinds
    db._derive_ai_api_kind(eng)
    assert get_settings(user)["ai_api_kind"] == "anthropic"


def test_save_settings_persists_ai_api_kind(user):
    save_settings(user, {"ai_api_kind": "gemini"})
    assert get_settings(user)["ai_api_kind"] == "gemini"
