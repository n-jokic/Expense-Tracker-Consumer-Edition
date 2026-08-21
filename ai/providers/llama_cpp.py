"""
ai/providers/llama_cpp.py — local Gemma provider (wraps llm.py).
"""

from __future__ import annotations

from ai.providers.base import AIProvider, GenerationRequest, GenerationResult, ProviderCapabilities


class LlamaCppProvider:
    """Adapter over llm.py's local (llama-cpp) engine."""

    capabilities = ProviderCapabilities(native_tool_calls=False, json_schema=True, vision=False, max_context=2048)

    def __init__(self, settings: dict):
        self.settings = settings

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from llm import _local_chat  # reuse llm.py's local chat helper

        res = _local_chat(self.settings, request.system, request.user, request.max_tokens)
        return GenerationResult(text=res.text, diagnostic=res.diagnostic)
