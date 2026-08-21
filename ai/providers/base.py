"""
ai/providers/base.py — provider interface + capabilities (Phase 3 A1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderCapabilities:
    native_tool_calls: bool = False
    json_schema: bool = False
    vision: bool = False
    max_context: int | None = None


@dataclass
class GenerationRequest:
    system: str
    user: str
    max_tokens: int = 256
    # AI-04: True when the caller expects STRICT JSON back (planner / repair
    # turns). Providers may map this to their native structured-output mode;
    # it is never set for prose composition requests.
    wants_json: bool = False


@dataclass
class GenerationResult:
    text: str | None
    diagnostic: str = ""


@runtime_checkable
class AIProvider(Protocol):
    capabilities: ProviderCapabilities

    def generate(self, request: GenerationRequest) -> GenerationResult:  # pragma: no cover
        ...
