"""
ai/providers/gemini.py — native Google Gemini adapter (#22).

Talks to the Generative Language API directly: POST
{base}/models/{model}:generateContent with an x-goog-api-key header.
Same house rules as the other providers:

* every outbound body passes the AI-01 sanitizer boundary first;
* transient 429/5xx failures get the same bounded retries (AI-03),
  honoring a capped Retry-After;
* capabilities never claim unimplemented features.
"""

from __future__ import annotations

import logging

import requests

from ai.providers.base import (
    AIProvider,
    GenerationRequest,
    GenerationResult,
    ProviderCapabilities,
)
from ai.safety import sanitize_outbound_text

log = logging.getLogger("ai.providers.gemini")

_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_MODEL = "gemini-2.0-flash"

# Same outage policy as llm._api_chat / anthropic (AI-03).
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_RETRY_AFTER_CAP_S = 8.0
_backoff_sleep = __import__("time").sleep   # module-level: tests monkeypatch this


class GeminiProvider:
    """Native Gemini over generateContent. Requires settings keys:
    ai_api_key_enc (Google AI Studio key), optional ai_api_model."""

    capabilities = ProviderCapabilities(native_tool_calls=False, json_schema=False,
                                        vision=False, max_context=1_000_000)

    def __init__(self, settings: dict):
        self.settings = settings or {}

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from crypto import decrypt_str

        key = decrypt_str(self.settings.get("ai_api_key_enc") or "")
        if not key:
            return GenerationResult(
                None, "No Google AI API key configured — add it in "
                      "Settings → Notifications → AI assistant.")
        base = str(self.settings.get("ai_api_base") or _DEFAULT_BASE).rstrip("/")
        if "generativelanguage" not in base:
            base = _DEFAULT_BASE          # a foreign base URL is ignored
        model_name = str(self.settings.get("ai_api_model") or _DEFAULT_MODEL)
        # AI-01 boundary: redact before anything is serialized.
        system = sanitize_outbound_text(request.system)
        user = sanitize_outbound_text(request.user)
        gen_cfg = {"maxOutputTokens": int(request.max_tokens),
                   "temperature": 0.7}
        wants_json = bool(getattr(request, "wants_json", False))
        if wants_json:
            # strict-JSON (planner) turns: ask for a JSON mime response.
            gen_cfg["responseMimeType"] = "application/json"
        body = {"contents": [{"role": "user",
                              "parts": [{"text": user}]}],
                "systemInstruction": {"parts": [{"text": system}]},
                "generationConfig": gen_cfg}
        headers = {"x-goog-api-key": key,
                   "Content-Type": "application/json"}
        url = f"{base}/models/{model_name}:generateContent"
        diag = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = requests.post(url, headers=headers, json=body,
                                     timeout=20)
                status = getattr(resp, "status_code", None)
                if status is not None and status in _TRANSIENT_STATUS:
                    if attempt < _MAX_ATTEMPTS - 1:
                        retry_after = resp.headers.get("Retry-After")
                        try:
                            delay = min(float(retry_after), _RETRY_AFTER_CAP_S)
                        except (TypeError, ValueError):
                            delay = min(2.0 ** attempt, _RETRY_AFTER_CAP_S)
                        log.info("Gemini transient %s (attempt %d/%d)",
                                 status, attempt + 1, _MAX_ATTEMPTS)
                        diag = f"provider returned {status}; retrying"
                        _backoff_sleep(delay)
                        continue
                    diag = ("Gemini is temporarily unavailable "
                            f"({status}). Please try again shortly.")
                    break
                resp.raise_for_status()
                data = resp.json()
                cands = data.get("candidates") or []
                parts = (((cands[0] or {}).get("content") or {})
                         .get("parts") or [])
                text = "".join(p.get("text", "") for p in parts
                               if isinstance(p, dict)).strip()
                return GenerationResult(text=text or None)
            except requests.exceptions.HTTPError as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (401, 403):
                    diag = ("The Google AI API key was rejected — check it in "
                            "Settings → Notifications → AI assistant.")
                else:
                    diag = ("The Gemini request failed — check the API key in "
                            "Settings → Notifications → AI assistant.")
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt < _MAX_ATTEMPTS - 1:
                    delay = min(2.0 ** attempt, _RETRY_AFTER_CAP_S)
                    diag = "provider unreachable; retrying"
                    _backoff_sleep(delay)
                    continue
                diag = "Gemini could not be reached — check your connection."
                break
            except Exception as e:
                log.warning("Gemini request failed (%s): %s",
                            type(e).__name__, e)
                diag = ("The Gemini request failed — check the API key in "
                        "Settings → Notifications → AI assistant.")
                break
        return GenerationResult(None, diag)
