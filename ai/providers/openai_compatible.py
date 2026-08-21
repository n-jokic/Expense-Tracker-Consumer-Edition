"""
ai/providers/openai_compatible.py — OpenRouter / OpenAI-compatible provider.
"""

from __future__ import annotations

from ai.providers.base import AIProvider, GenerationRequest, GenerationResult, ProviderCapabilities


class OpenAICompatibleProvider:
    # AI-02: requests are prompt-only JSON — no native tool calls and no
    # structured json_schema endpoint are implemented, so the capabilities
    # must not claim them.
    capabilities = ProviderCapabilities(native_tool_calls=False, json_schema=False,
                                        vision=False, max_context=8192)

    def __init__(self, settings: dict):
        self.settings = settings

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from llm import _api_chat

        res = _api_chat(self.settings, request.system, request.user,
                        request.max_tokens,
                        json_mode=bool(getattr(request, "wants_json", False)))
        return GenerationResult(text=res.text, diagnostic=res.diagnostic)
