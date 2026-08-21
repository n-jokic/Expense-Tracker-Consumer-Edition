"""
ai/router.py — deterministic fast-route + planner stub (Phase 3 A5).

Deterministic aggregates bypass the LLM; local Gemma planner outputs
constrained JSON {tool, arguments} validated before execution.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ai.tool_registry import TOOLS

MAX_TOOL_CALLS = 4
MAX_RESULT_ROWS = 100

# Very small deterministic routing table for scaffold
DETERMINISTIC_PATTERNS: list[tuple[str, str]] = [
    (r"how much.*spent.*this month", "aggregate_spending"),
    (r"budget.*left|budget.*remaining", "budget_status"),
    (r"subscriptions?|recurring", "recurring_costs"),
]

def fast_route(question: str) -> str | None:
    q = question.lower()
    for pat, tool in DETERMINISTIC_PATTERNS:
        if re.search(pat, q):
            return tool
    return None


def parse_local_tool_json(text: str) -> dict | None:
    """Parse constrained {tool, arguments} JSON from local model output."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "tool" in obj and obj["tool"] in TOOLS:
            return obj
    except Exception:
        pass
    # Try to extract first JSON object substring
    try:
        m = re.search(r"\{.*?\}", text, re.S)
        if m:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "tool" in obj and obj["tool"] in TOOLS:
                return obj
    except Exception:
        pass
    return None
