"""
tests/test_ai_sanitizer.py — AI-01 outbound privacy sanitizer.

Covers the single sanitizer boundary in ai/safety.py that every payload must
pass before reaching an external provider:

  - string-level redaction categories: key-like strings / tokens,
    absolute user paths (home dir + workspace), emails
  - structured tool-result policy: sensitive-keyed values redacted in BOTH
    modes; id-like keys neutralized only for external providers; local
    provider mode keeps local context but still strips credentials
  - deterministic output and idempotency
  - debug logs carry counts only, never secret values
  - integration: llm._api_chat (the single egress point) calls the sanitizer
    and the serialized request body is clean; llm._local_chat keeps local
    context; orchestrator external vs local prompt building

All hermetic: no network, no real model.
"""

import json
import logging

from crypto import encrypt_str
import ai.safety as safety
import ai.orchestrator as orch
import ai.tool_registry as tr
import llm

SECRET = "sk-proj-abc123XYZdef456ghi789"
EMAIL = "paul.doe@example.com"
WIN_PATH = "C:\\Users\\paul\\Desktop\\gitProjects\\expense\\data.csv"
POSIX_PATH = "/home/paul/reports/march.pdf"


# ── Category: key-like strings / tokens ──────────────────────────────────────

def test_redacts_openai_style_key():
    out = safety.sanitize_outbound_text(f"my key is {SECRET} ok")
    assert SECRET not in out
    assert safety.REDACTED_CREDENTIAL in out
    assert "my key is" in out and "ok" in out


def test_redacts_github_slack_aws_tokens():
    slack_token = "xo" + "xb-" + "a" * 20
    text = ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 "
            f"{slack_token} "
            "AKIAIOSFODNN7EXAMPLE")
    out = safety.sanitize_outbound_text(text)
    for token in ("ghp_ABCDEFGH", slack_token[:10], "AKIAIOSFODNN7"):
        assert token not in out
    assert out.count(safety.REDACTED_CREDENTIAL) == 3


def test_redacts_jwt_and_hex_secrets():
    jwt = ("eyJhbGciOiJIUzI1NiJ9."
           "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
           "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
    hex_secret = "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8"
    out = safety.sanitize_outbound_text(f"auth {jwt} digest {hex_secret}")
    assert jwt not in out
    assert hex_secret not in out


def test_redacts_bearer_header():
    out = safety.sanitize_outbound_text("Authorization: Bearer abc123def456ghi789")
    assert "abc123def456ghi789" not in out
    assert safety.REDACTED_CREDENTIAL in out


def test_keyword_assignment_keeps_keyword_redacts_value():
    out = safety.sanitize_outbound_text("api_key=sk-live-abcdefgh123456")
    assert out == "api_key=[CREDENTIAL_REDACTED]"

    out2 = safety.sanitize_outbound_text('"token": "abc123def456"')
    assert "abc123def456" not in out2
    assert "token" in out2  # keyword preserved so structure stays readable

    out3 = safety.sanitize_outbound_text("password = hunter2pass")
    assert "hunter2pass" not in out3


def test_compound_api_token_key_is_caught():
    # api_token: the underscore must not defeat the keyword match
    out = safety.sanitize_outbound_text('"api_token": "abcd1234wxyz"')
    assert "abcd1234wxyz" not in out


def test_normal_words_are_not_mangled():
    out = safety.sanitize_outbound_text(
        "Total spent on groceries was 42.10 EUR at Lidl on 2026-08-21.")
    assert out == "Total spent on groceries was 42.10 EUR at Lidl on 2026-08-21."


# ── Category: absolute user paths ────────────────────────────────────────────

def test_redacts_windows_home_path():
    out = safety.sanitize_outbound_text(f"saved to {WIN_PATH} done")
    assert WIN_PATH not in out
    assert "C:\\Users\\paul" not in out
    assert safety.REDACTED_PATH in out
    assert "saved to" in out and "done" in out


def test_redacts_posix_home_paths():
    for p in (POSIX_PATH, "/Users/ann/Documents/budget.xlsx"):
        out = safety.sanitize_outbound_text(f"see {p} please")
        assert p not in out
        assert safety.REDACTED_PATH in out


def test_redacts_root_path():
    out = safety.sanitize_outbound_text("key file /root/.ssh/id_rsa leaked")
    assert "/root/.ssh/id_rsa" not in out
    assert safety.REDACTED_PATH in out


def test_path_trailing_sentence_punctuation_survives():
    out = safety.sanitize_outbound_text(f"stored under {POSIX_PATH}.")
    assert out == f"stored under {safety.REDACTED_PATH}."


# ── Category: emails ─────────────────────────────────────────────────────────

def test_redacts_email_addresses():
    out = safety.sanitize_outbound_text(f"contact {EMAIL} about the total")
    assert EMAIL not in out
    assert safety.REDACTED_EMAIL in out
    assert "contact" in out and "about the total" in out


def test_multiple_emails_all_redacted():
    out = safety.sanitize_outbound_text("a@x.io and b@y.org wrote")
    assert "@" not in out.replace(safety.REDACTED_EMAIL, "")
    assert out.count(safety.REDACTED_EMAIL) == 2


# ── Determinism + idempotency ────────────────────────────────────────────────

_NASTY = (f"key {SECRET}; bearer Bearer abc123def456ghi789; home {WIN_PATH}; "
          f"posix {POSIX_PATH}; mail {EMAIL}; api_key=supersecretvalue123")


def test_output_is_deterministic():
    once = safety.sanitize_outbound_text(_NASTY)
    twice = safety.sanitize_outbound_text(_NASTY)
    assert once == twice


def test_output_is_idempotent():
    once = safety.sanitize_outbound_text(_NASTY)
    twice = safety.sanitize_outbound_text(once)
    assert once == twice
    # markers themselves are stable under re-sanitization
    assert safety.sanitize_outbound_text(
        f"{safety.REDACTED_CREDENTIAL} {safety.REDACTED_PATH} "
        f"{safety.REDACTED_EMAIL}") == (
        f"{safety.REDACTED_CREDENTIAL} {safety.REDACTED_PATH} "
        f"{safety.REDACTED_EMAIL}")


def test_empty_and_none_inputs():
    assert safety.sanitize_outbound_text("") == ""
    assert safety.sanitize_outbound_text(None) == ""
    assert safety.strip_credentials(None) == ""
    assert safety.sanitize_outbound_text(123) == "123"


def test_counts_are_reported_per_category():
    out, counts = safety.redact_with_counts(_NASTY)
    assert counts.get("openai_style_key", 0) >= 1
    assert counts.get("bearer_token", 0) >= 1
    assert counts.get("windows_home_path", 0) >= 1
    assert counts.get("posix_home_path", 0) >= 1
    assert counts.get("email", 0) >= 1
    assert sum(counts.values()) >= 5
    assert SECRET not in out


# ── Log hygiene: counts only, never values ───────────────────────────────────

def test_debug_log_has_counts_never_values(caplog):
    with caplog.at_level(logging.DEBUG, logger="ai.safety"):
        safety.sanitize_outbound_text(_NASTY)
    assert "redactions" in caplog.text          # something was logged
    assert SECRET not in caplog.text            # never the secret...
    assert EMAIL not in caplog.text             # ...nor any redacted value
    assert WIN_PATH not in caplog.text
    assert "hunter2pass" not in caplog.text if "hunter2pass" in _NASTY else True


def test_no_log_output_when_clean(caplog):
    with caplog.at_level(logging.DEBUG, logger="ai.safety"):
        safety.sanitize_outbound_text("nothing sensitive here")
    assert "redactions" not in caplog.text


# ── Structured tool-result policy ────────────────────────────────────────────

def _sample_result():
    return {
        "id": 77,
        "user_id": 42,
        "description": f"contact {EMAIL}",
        "receipt_path": WIN_PATH,
        "total_eur": 9.99,
        "api_key": "sk-abcdefghijklmnop1234",
        "_provenance": {"calculation": "search_transactions", "row_count": 1},
    }


def test_external_mode_removes_ids_paths_emails_and_sensitive_fields():
    ext = safety.sanitize_tool_result(_sample_result(), external=True)
    assert ext["id"] == safety.REDACTED_VALUE
    assert ext["user_id"] == safety.REDACTED_VALUE
    assert EMAIL not in ext["description"]
    assert ext["receipt_path"] == safety.REDACTED_PATH
    assert ext["api_key"] == safety.REDACTED_VALUE
    # legitimate answer fields survive untouched
    assert ext["total_eur"] == 9.99
    assert ext["_provenance"]["row_count"] == 1
    assert ext["_provenance"]["calculation"] == "search_transactions"


def test_local_mode_keeps_context_but_strips_credentials():
    loc = safety.sanitize_tool_result(_sample_result(), external=False)
    assert loc["id"] == 77                       # identifiers kept locally
    assert loc["user_id"] == 42
    assert EMAIL in loc["description"]           # local context preserved
    assert loc["receipt_path"] == WIN_PATH
    assert loc["api_key"] == safety.REDACTED_VALUE   # credentials never pass
    assert loc["total_eur"] == 9.99


def test_default_mode_fails_closed_to_external():
    out = safety.sanitize_tool_result({"note": f"mail {EMAIL}"})
    assert EMAIL not in out["note"]


def test_nested_records_in_lists_are_treated():
    res = {"expenses": [
        {"id": 1, "description": "Lidl run", "amount_eur": 3.5},
        {"id": 2, "description": f"refund query to {EMAIL}"},
    ]}
    ext = safety.sanitize_tool_result(res, external=True)
    assert ext["expenses"][0]["id"] == safety.REDACTED_VALUE
    assert ext["expenses"][0]["amount_eur"] == 3.5
    assert EMAIL not in ext["expenses"][1]["description"]
    loc = safety.sanitize_tool_result(res, external=False)
    assert loc["expenses"][0]["id"] == 1


def test_tuple_container_preserved_with_credential_stripped():
    res = {"items": (f"tok {SECRET}", "plain")}
    out = safety.sanitize_tool_result(res, external=False)
    assert isinstance(out["items"], tuple)
    assert SECRET not in out["items"][0]
    assert out["items"][1] == "plain"


def test_descriptions_remain_capped():
    res = {"description": "x" * 500}
    for ext in (True, False):
        out = safety.sanitize_tool_result(res, external=ext)
        assert len(out["description"]) == safety.MAX_UNSANITIZED_STR_LEN


def test_structured_walk_is_deterministic_and_idempotent():
    once = safety.sanitize_tool_result(_sample_result(), external=True)
    twice = safety.sanitize_tool_result(once, external=True)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


# ── Integration A: llm._api_chat — the single egress point ───────────────────

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_api_chat_calls_sanitizer_and_body_is_clean(monkeypatch):
    """Spy + intercepted request: the outbound serialization path must call
    the sanitizer and forbidden values must never reach the request body."""
    calls = []
    real = safety.sanitize_outbound_text

    def spy(text):
        calls.append(str(text))
        return real(text)

    monkeypatch.setattr(llm, "sanitize_outbound_text", spy)
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["body"] = json
        return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    settings = {"ai_provider": "api",
                "ai_api_base": "https://example.com/v1",
                "ai_api_model": "gemma-test",
                "ai_api_key_enc": encrypt_str("sk-real-key")}
    system = f"You are helpful. The configured key is {SECRET}."
    user = (f"I live at {WIN_PATH} and my mail is {EMAIL}; "
            "how much did I spend? Total was 12 EUR.")

    result = llm._api_chat(settings, system, user, 64)

    assert result.text == "ok"
    assert calls == [system, user]  # sanitizer invoked on both messages
    body = json.dumps(captured["body"])
    assert SECRET not in body
    assert EMAIL not in body
    assert "C:\\Users\\paul" not in body
    assert "12 EUR" in body  # legitimate financial content still flows


def test_generate_summary_outbound_body_is_sanitized(monkeypatch):
    """The non-orchestrator llm.py callers are covered by the same choke
    point: hostile stats cannot leak a path into an external body."""
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["body"] = json
        return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    settings = {"ai_provider": "api", "ai_api_key_enc": encrypt_str("sk-x")}
    llm.generate_summary({"total_eur": 1.0,
                          "top_categories": [f"receipts from {WIN_PATH}"]},
                         settings)
    body = json.dumps(captured["body"])
    assert WIN_PATH not in body
    assert safety.REDACTED_PATH in body


# ── Integration B: local provider keeps context, strips credentials ─────────

def test_local_chat_keeps_local_context_but_strips_credentials(monkeypatch):
    captured = {}

    class FakeModel:
        def create_chat_completion(self, **kw):
            captured["messages"] = kw["messages"]
            return {"choices": [{"message": {"content": "local ok"}}]}

    monkeypatch.setattr(llm, "_get_local_model", lambda s: FakeModel())
    system = f"system prompt with {SECRET}"
    user = (f"home {WIN_PATH}, mail {EMAIL}, spent 20 EUR")

    result = llm._local_chat({"ai_provider": "local",
                              "ai_local_model": "m.gguf"},
                             system, user, 32)

    assert result.text == "local ok"
    blob = " ".join(m["content"] for m in captured["messages"])
    assert SECRET not in blob                    # credentials stripped locally
    assert safety.REDACTED_CREDENTIAL in blob
    assert WIN_PATH in blob                      # local context preserved
    assert EMAIL in blob
    assert "20 EUR" in blob


# ── Integration C: orchestrator prompt building per provider kind ────────────

class _CapturingProvider:
    """Records GenerationRequests; returns a fixed safe answer."""

    def __init__(self):
        self.seen = []

    def generate(self, request):
        from ai.providers.base import GenerationResult
        self.seen.append(request)
        return GenerationResult(text="Answer: 9.99 EUR", diagnostic="")


def _tool_call_with_forbidden_fields():
    from ai.schemas import AdvisorToolCall
    return AdvisorToolCall(
        tool="search_transactions",
        arguments={"query": "lidl"},
        result={
            "count": 1,
            "expenses": [{"id": 77, "user_id": 1, "date": "2026-08-01",
                          "description": f"contact {EMAIL}",
                          "category": "Groceries", "amount_eur": 9.99,
                          "receipt_path": WIN_PATH}],
            "_provenance": {"calculation": "search_transactions",
                            "row_count": 1},
        },
    )


def test_compose_answer_external_prompt_is_fully_redacted(monkeypatch):
    prov = _CapturingProvider()
    monkeypatch.setattr(orch, "_get_provider", lambda s: prov)
    settings = {"ai_provider": "api", "ai_api_key_enc": encrypt_str("k")}

    answer, diag = orch._compose_answer("q?", [_tool_call_with_forbidden_fields()],
                                        settings)

    assert answer == "Answer: 9.99 EUR"
    req = prov.seen[0]
    # Unescape the embedded-JSON backslashes so the absence checks cannot be
    # fooled by escaping.
    blob = (req.system + req.user).replace("\\\\", "\\")
    assert EMAIL not in blob
    assert WIN_PATH not in blob
    assert '"id": 77' not in blob
    assert '"user_id": 1' not in blob
    assert "9.99" in blob  # the numbers needed for the answer survive


def test_compose_answer_local_prompt_keeps_context(monkeypatch):
    prov = _CapturingProvider()
    monkeypatch.setattr(orch, "_get_provider", lambda s: prov)
    settings = {"ai_provider": "local", "ai_local_model": "m.gguf"}

    answer, diag = orch._compose_answer("q?", [_tool_call_with_forbidden_fields()],
                                        settings)

    assert answer == "Answer: 9.99 EUR"
    # The prompt embeds the tool result as JSON, so backslashes are doubled;
    # unescape before checking that the local path really is preserved.
    blob = (prov.seen[0].system + prov.seen[0].user).replace("\\\\", "\\")
    assert EMAIL in blob
    assert WIN_PATH in blob
    assert '"id": 77' in blob


def test_orchestrate_external_request_never_contains_forbidden_fields(monkeypatch):
    """End-to-end intercepted-request proof (plan AI-01 verification): with an
    API provider configured, orchestrate() → compose → _api_chat must never
    put forbidden fields into the outbound body."""
    def fake_search(user_id, query=None, **kwargs):
        return {
            "count": 1,
            "expenses": [{"id": 77, "user_id": 1, "date": "2026-08-01",
                          "description": f"Lidl refund contact {EMAIL}",
                          "category": "Groceries", "amount_eur": 9.99,
                          "receipt_path": WIN_PATH}],
            "_provenance": {"calculation": "search_transactions",
                            "row_count": 1},
        }

    monkeypatch.setitem(tr.TOOLS, "search_transactions", fake_search)
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["body"] = json
        return _FakeResp({"choices": [{"message": {"content":
                                       "You spent 9.99 EUR."}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    settings = {"ai_provider": "api", "ai_api_key_enc": encrypt_str("sk-x")}

    res = orch.orchestrate(1, "What did I spend at Lidl?", settings)

    assert res.get("answer")
    body = json.dumps(captured["body"])
    assert EMAIL not in body
    assert WIN_PATH not in body
    assert '"id": 77' not in body
    assert '"user_id": 1' not in body
