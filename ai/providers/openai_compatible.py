"""
ai/providers/openai_compatible.py — OpenRouter / OpenAI-compatible provider.
"""

from __future__ import annotations

from ai.providers.base import AIProvider, GenerationRequest, GenerationResult, ProviderCapabilities


class OpenAICompatibleProvider:
    capabilities = ProviderCapabilities(native_tool_calls=True, json_schema=True, vision=False, max_context=8192)

    def __init__(self, settings: dict):
        self.settings = settings

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from llm import _api_chat

        res = _api_chat(self.settings, request.system, request.user, request.max_tokens)
        return GenerationResult(text=res.text, diagnostic=res.diagnostic)
