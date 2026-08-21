"""
ai/orchestrator.py — bounded orchestrator loop (Phase 3 A4).

Deterministic fast-route → intent/planner → schema validation → tool (≤4 calls)
→ answer composer with provenance. No unconstrained ReAct.
"""

from __future__ import annotations

from ai.router import MAX_TOOL_CALLS, fast_route
from ai.tool_registry import TOOLS
from ai.schemas import FinanceToolResult


def orchestrate(user_id: int, question: str, settings: dict) -> dict:
    """Run one advisor turn. Returns {answer, provenance, tool_calls}."""
    tool_name = fast_route(question)
    if tool_name and tool_name in TOOLS:
        # Deterministic path: execute one tool with best-effort args
        try:
            # Minimal arg inference for scaffold — real impl uses A5 planner
            result = TOOLS[tool_name](user_id=user_id, year=2025, month=6) if "year" in TOOLS[tool_name].__code__.co_varnames else TOOLS[tool_name](user_id=user_id)
            return {"answer": str(result), "tool": tool_name, "provenance": result.get("_provenance")}
        except Exception as e:
            return {"answer": None, "error": str(e), "tool": tool_name}

    # Fallback: ask LLM planner (stub)
    return {"answer": None, "error": "No deterministic route and planner not yet wired — scaffold turn.", "tool": None}
