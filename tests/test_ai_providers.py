"""
AI-04 slice-2 regression tests — native provider adapters and disclosure.

Direct OpenAI gets its structured (json_object) planner path; Claude is a
separate adapter with its own authentication, endpoint and outage policy;
existing OpenAI-compatible endpoints remain supported; connection tests
disclose exactly what data may leave the device.
"""

import requests
import pytest

import llm
from ai.providers.base import GenerationRequest


# ── Direct OpenAI structured path ────────────────────────────────────────────

@pytest.fixture()
def no_key_needed(monkeypatch):
    monkeypatch.setattr(llm, "decrypt_str", lambda s: "test-key")


@pytest.fixture()
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(llm, "_backoff_sleep", lambda s: recorded.append(s))
    return recorded


def test_openai_native_gets_json_response_format_on_planner_turns(
        no_key_needed, sleeps, monkeypatch):
    bodies = []

    def fake_post(url, headers=None, json=None, timeout=None):
        bodies.append((url, json))
        return requests.models.Response()  # will raise in .json — see below

    class OkResp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{\"tool\": \"forecast\"}"}}]}

    monkeypatch.setattr(llm.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        bodies.append((url, json)) or OkResp())
    settings = {"ai_api_base": "https://api.openai.com/v1",
                "ai_api_model": "gpt-4o-mini"}
    llm._api_chat(settings, "sys", "user", 64, json_mode=True)
    url, body = bodies[0]
    assert "api.openai.com" in url
    assert body["response_format"] == {"type": "json_object"}


def test_non_openai_base_and_prose_turns_stay_prompt_only(
        no_key_needed, sleeps, monkeypatch):
    bodies = []

    class OkResp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "prose"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        bodies.append((url, json))
        return OkResp()

    monkeypatch.setattr(llm.requests, "post", fake_post)

    llm._api_chat({"ai_api_base": "https://openrouter.ai/api/v1",
                   "ai_api_model": "m"}, "sys", "user", 64, json_mode=True)
    assert "response_format" not in bodies[0][1]

    llm._api_chat({"ai_api_base": "https://api.openai.com/v1",
                   "ai_api_model": "m"}, "sys", "user", 64, json_mode=False)
    assert "response_format" not in bodies[1][1]


# ── Anthropic adapter contract ───────────────────────────────────────────────

class AnthropicOk:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"content": [{"type": "text", "text": " hello from claude "}]}


def test_anthropic_headers_endpoint_and_parsing(monkeypatch):
    import ai.providers.anthropic as ant

    captured = {}

    class Resp(AnthropicOk):
        pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update({"url": url, "headers": headers, "body": json})
        return Resp()

    monkeypatch.setattr(ant.requests, "post", fake_post)
    monkeypatch.setattr(ant, "_backoff_sleep", lambda s: None)
    from ai.providers.anthropic import AnthropicProvider
    provider = AnthropicProvider({"ai_api_key_enc": "enc",
                                  "ai_api_model": "claude-3-5-haiku-latest"})
    # crypto.decrypt_str is module-level in the adapter's import site
    import crypto
    orig = crypto.decrypt_str
    crypto.decrypt_str = lambda s: "sk-ant-test"
    try:
        res = provider.generate(GenerationRequest(system="s", user="u", max_tokens=64))
    finally:
        crypto.decrypt_str = orig
    assert res.text == "hello from claude"
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert "anthropic-version" in captured["headers"]
    assert captured["body"]["system"] == "s"
    assert captured["body"]["messages"] == [{"role": "user", "content": "u"}]


def test_anthropic_sanitizes_outbound_payload(monkeypatch):
    """The AI-01 boundary holds for the Claude adapter too."""
    import ai.providers.anthropic as ant
    import crypto

    captured = {}
    monkeypatch.setattr(ant.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        captured.update(body=json) or AnthropicOk())
    monkeypatch.setattr(ant, "_backoff_sleep", lambda s: None)
    secret = "sk-ant-" + "a" * 40
    monkeypatch.setattr(crypto, "decrypt_str", lambda s: secret)

    from ai.providers.anthropic import AnthropicProvider
    provider = AnthropicProvider({})
    res = provider.generate(GenerationRequest(
        system="s", user=f"my key is {secret} — summarize", max_tokens=32))
    assert res.text == "hello from claude"
    assert secret not in captured["body"]["messages"][0]["content"]


def test_anthropic_transient_retry_then_success(monkeypatch):
    import ai.providers.anthropic as ant
    import crypto

    calls = []
    sleeps = []
    monkeypatch.setattr(ant, "_backoff_sleep", sleeps.append)

    class R:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                e = requests.exceptions.HTTPError(str(self.status_code))
                e.response = self
                raise e

        def json(self):
            return {"content": [{"type": "text", "text": "ok"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return R(503) if len(calls) < 2 else R(200)

    monkeypatch.setattr(ant.requests, "post", fake_post)
    monkeypatch.setattr(crypto, "decrypt_str", lambda s: "k")
    from ai.providers.anthropic import AnthropicProvider
    res = AnthropicProvider({}).generate(
        GenerationRequest(system="s", user="u"))
    assert res.text == "ok"
    assert len(calls) == 2 and len(sleeps) == 1


def test_anthropic_persistent_503_diagnoses_temporary_unavailability(monkeypatch):
    import ai.providers.anthropic as ant
    import crypto

    calls = []
    monkeypatch.setattr(ant, "_backoff_sleep", lambda s: None)

    class R:
        status_code = 503
        headers = {}

        def raise_for_status(self):
            e = requests.exceptions.HTTPError("503")
            e.response = self
            raise e

    monkeypatch.setattr(ant.requests, "post", lambda *a, **k: calls.append(1) or R())
    monkeypatch.setattr(crypto, "decrypt_str", lambda s: "k")
    from ai.providers.anthropic import AnthropicProvider
    res = AnthropicProvider({}).generate(
        GenerationRequest(system="s", user="u"))
    assert res.text is None
    assert len(calls) == 3
    assert "temporarily unavailable" in res.diagnostic


def test_anthropic_capabilities_honest():
    from ai.providers.anthropic import AnthropicProvider
    caps = AnthropicProvider.capabilities
    assert caps.native_tool_calls is False
    assert caps.json_schema is False


def test_orchestrator_routes_by_api_kind(monkeypatch):
    import ai.orchestrator as orch
    monkeypatch.setattr(orch, "resolve_provider", lambda s: "api", raising=False)
    # NB: _get_provider imports resolve_provider from llm inside the function;
    # patch the llm module attribute it actually reads.
    import llm
    monkeypatch.setattr(llm, "resolve_provider", lambda s: "api")

    from ai.providers.anthropic import AnthropicProvider
    from ai.providers.openai_compatible import OpenAICompatibleProvider

    p1 = orch._get_provider({"ai_provider": "api", "ai_api_kind": "anthropic"})
    assert isinstance(p1, AnthropicProvider)
    p2 = orch._get_provider({"ai_provider": "api", "ai_api_kind": "openai_compatible"})
    assert isinstance(p2, OpenAICompatibleProvider)


# ── Connection-test disclosure (settings UI) ────────────────────────────────

def test_settings_disclose_what_leaves_the_device():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath(
        "app_pages", "settings_ai.py").read_text(encoding="utf-8")
    assert "What this test sends to the external" in src
    assert "redacted first. Raw transactions never leave the device." in src
    assert "Nothing leaves the device with the" in src