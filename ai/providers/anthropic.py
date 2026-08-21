"""
ai/providers/anthropic.py — native Claude adapter (AI-04).

A separate provider with its OWN authentication (x-api-key + version
header) and its own /v1/messages endpoint. Same house rules as the other
providers:

* every outbound body passes the AI-01 sanitizer boundary first;
* transient 429/5xx failures get the same bounded retries as the
  OpenAI-compatible path (AI-03), honoring a capped Retry-After;
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

log = logging.getLogger("ai.providers.anthropic")

ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_BASE = "https://api.anthropic.com"

# Same outage policy as llm._api_chat (AI-03).
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_RETRY_AFTER_CAP_S = 8.0
_backoff_sleep = __import__("time").sleep   # module-level: tests monkeypatch this


class AnthropicProvider:
    """Native Claude over /v1/messages. Requires settings keys:
    ai_api_key_enc (Anthropic key), optional ai_api_base, ai_api_model."""

    capabilities = ProviderCapabilities(native_tool_calls=False, json_schema=False,
                                        vision=False, max_context=200_000)

    def __init__(self, settings: dict):
        self.settings = settings or {}

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from crypto import decrypt_str

        key = decrypt_str(self.settings.get("ai_api_key_enc") or "")
        if not key:
            return GenerationResult(
                None, "No Anthropic API key configured — add it in "
                      "Settings → Notifications → AI assistant.")
        base = str(self.settings.get("ai_api_base") or _DEFAULT_BASE).rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        model_name = str(self.settings.get("ai_api_model") or
                         "claude-3-5-haiku-latest")
        # AI-01 boundary: redact before anything is serialized.
        system = sanitize_outbound_text(request.system)
        user = sanitize_outbound_text(request.user)
        body = {"model": model_name,
                "max_tokens": int(request.max_tokens),
                "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": key,
                   "anthropic-version": ANTHROPIC_VERSION,
                   "Content-Type": "application/json"}
        diag = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = requests.post(f"{base}/messages", headers=headers,
                                     json=body, timeout=20)
                status = getattr(resp, "status_code", None)
                if status is not None and status in _TRANSIENT_STATUS:
                    if attempt < _MAX_ATTEMPTS - 1:
                        retry_after = resp.headers.get("Retry-After")
                        try:
                            delay = min(float(retry_after), _RETRY_AFTER_CAP_S)
                        except (TypeError, ValueError):
                            delay = min(2.0 ** attempt, _RETRY_AFTER_CAP_S)
                        log.info("Anthropic transient %s (attempt %d/%d)",
                                 status, attempt + 1, _MAX_ATTEMPTS)
                        diag = f"provider returned {status}; retrying"
                        _backoff_sleep(delay)
                        continue
                    diag = ("Claude is temporarily unavailable "
                            f"({status}). Please try again shortly.")
                    break
                resp.raise_for_status()
                data = resp.json()
                blocks = data.get("content") or []
                text = "".join(b.get("text", "") for b in blocks
                               if isinstance(b, dict)).strip()
                return GenerationResult(text=text or None)
            except requests.exceptions.HTTPError as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (401, 403):
                    diag = ("The Anthropic API key was rejected — check it in "
                            "Settings → Notifications → AI assistant.")
                else:
                    diag = ("The Claude request failed — check the API key and "
                            "base URL in Settings → Notifications → AI assistant.")
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt < _MAX_ATTEMPTS - 1:
                    delay = min(2.0 ** attempt, _RETRY_AFTER_CAP_S)
                    diag = "provider unreachable; retrying"
                    _backoff_sleep(delay)
                    continue
                diag = "Claude could not be reached — check your connection."
                break
            except Exception as e:
                log.warning("Anthropic request failed (%s): %s",
                            type(e).__name__, e)
                diag = ("The Claude request failed — check the API key and "
                        "base URL in Settings → Notifications → AI assistant.")
                break
        return GenerationResult(None, diag)
