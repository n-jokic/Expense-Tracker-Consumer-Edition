"""
ai/router.py — deterministic fast-route + planner JSON parsing (Phase 3 A4/A5).

Deterministic aggregates bypass the LLM; local Gemma planner outputs
constrained JSON {tool, arguments} validated before execution. One repair
attempt on invalid JSON, then graceful fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any
from datetime import date

MAX_TOOL_CALLS = 4
MAX_RESULT_ROWS = 100

# Deterministic routing table — common questions skip the model planner entirely.
# Each pattern maps to a canonical tool; orchestrator infers args from question
# and current date without invoking the LLM.
DETERMINISTIC_PATTERNS: list[tuple[str, str]] = [
    (r"how much.*spen", "aggregate_spending"),
    (r"spen.*this month|this month.*spen", "aggregate_spending"),
    (r"spending.*this month", "aggregate_spending"),
    (r"budget.*left|budget.*remaining|remaining budget", "budget_status"),
    (r"how much budget", "budget_status"),
    (r"budget runway|days.*budget|budget.*days", "budget_runway"),
    (r"subscriptions?|recurring.*bill|recurring cost", "recurring_costs"),
    (r"recurring", "recurring_costs"),
    (r"income.*this month|this month.*income", "cashflow_summary"),
    (r"cashflow|cash flow", "cashflow_summary"),
    (r"savings.*status|savings goal|how.*savings", "savings_status"),
    (r"debt|loan.*summary|how much.*owe", "debt_summary"),
    (r"anomal|unusual.*spending|unusual.*expense", "anomalies"),
    (r"forecast|next month.*spend|predict.*spending", "forecast"),
]

# Lightweight period/category extraction for deterministic tool args.
_CATEGORY_HINTS = [
    "groceries", "dining out", "transport", "entertainment", "utilities",
    "healthcare", "shopping", "travel", "housing", "education", "other",
    "food & dining",
]

def _extract_category(question: str) -> str | None:
    q = question.lower()
    for cat in _CATEGORY_HINTS:
        if cat in q:
            # Return canonical capitalised form
            return cat.title() if cat != "food & dining" else "Food & Dining"
    return None


def fast_route(question: str) -> str | None:
    """Return a tool name for deterministic fast-path, or None to use planner."""
    q = question.lower()
    for pat, tool in DETERMINISTIC_PATTERNS:
        if re.search(pat, q):
            return tool
    return None


def _extract_json_object(text: str) -> dict | None:
    """Extract first JSON object substring and parse it."""
    # Try direct parse first
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Try to find JSON object with balanced braces (simple heuristic)
    # Look for outermost { ... } that parses
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def parse_local_tool_json(text: str) -> dict | None:
    """Parse constrained {tool, arguments} JSON from local model output.

    Validates that 'tool' is a known finance tool. Returns {"tool": ..., "arguments": ...}
    or None if invalid. Does NOT validate argument values — that is done by
    validate_tool_call.
    """
    if not text or not text.strip():
        return None
    # Lazy import to avoid circular
    try:
        from ai.tool_registry import TOOLS
    except Exception:
        TOOLS = {}
    obj = _extract_json_object(text)
    if obj is None:
        return None
    if not isinstance(obj, dict):
        return None
    tool = obj.get("tool")
    if not isinstance(tool, str) or tool not in TOOLS:
        return None
    args = obj.get("arguments", {})
    if not isinstance(args, dict):
        # Allow flat object where keys besides 'tool' are args
        args = {k: v for k, v in obj.items() if k != "tool"}
        if not isinstance(args, dict):
            return None
    # Ensure arguments is a dict
    if "arguments" in obj and isinstance(obj["arguments"], dict):
        args = obj["arguments"]
    return {"tool": tool, "arguments": args}


def validate_tool_call(tool: str, arguments: dict) -> tuple[bool, str | None]:
    """Validate tool name and argument types against TOOL_SCHEMAS if available.

    Returns (ok, error_message). This is structural validation only — value
    validation (e.g. unknown category) is handled by the domain layer and
    returned as a tool error, not a routing error.
    """
    try:
        from ai.tool_registry import TOOLS, TOOL_SCHEMAS
    except Exception:
        return True, None
    if tool not in TOOLS:
        return False, f"unknown tool: {tool}"
    schema = TOOL_SCHEMAS.get(tool) if "TOOL_SCHEMAS" in dir(__import__("ai.tool_registry", fromlist=["TOOL_SCHEMAS"])) else None
    # If no schema, accept any dict args
    try:
        from ai.tool_registry import TOOL_SCHEMAS as _schemas
        schema = _schemas.get(tool)
    except Exception:
        schema = None
    if schema is None:
        return True, None
    required = schema.get("required", [])
    for req in required:
        if req not in arguments:
            return False, f"missing required argument: {req}"
    # Type checks for known fields (lightweight)
    for k, v in arguments.items():
        if k in ("year", "month", "n", "limit", "term_months"):
            if not isinstance(v, int):
                try:
                    int(v)
                except Exception:
                    return False, f"argument {k} must be int"
        if k in ("total_budget_eur", "principal_eur", "annual_rate_pct", "extra_monthly_eur", "multiplier"):
            try:
                float(v)
            except Exception:
                return False, f"argument {k} must be numeric"
    return True, None


def infer_deterministic_args(tool: str, question: str, today: date | None = None) -> dict:
    """Infer minimal args for a deterministic tool call from question + date."""
    today = today or date.today()
    cat = _extract_category(question)
    if tool == "aggregate_spending":
        args: dict[str, Any] = {"year": today.year, "month": today.month}
        if cat:
            args["category"] = cat
        # Detect "last month" phrasing
        if "last month" in question.lower() or "previous month" in question.lower():
            if today.month == 1:
                args["year"] = today.year - 1
                args["month"] = 12
            else:
                args["month"] = today.month - 1
        return args
    if tool == "budget_status":
        return {"year": today.year, "month": today.month}
    if tool == "budget_runway":
        return {"total_budget_eur": 1000.0, "period_start": today.replace(day=1).isoformat()}
    if tool == "category_breakdown":
        return {"year": today.year, "month": today.month}
    if tool == "merchant_breakdown":
        return {"year": today.year, "month": today.month, "n": 5}
    if tool == "cashflow_summary":
        return {"year": today.year, "month": today.month}
    if tool in ("recurring_costs", "savings_status", "debt_summary", "forecast", "anomalies", "subscription_changes"):
        return {}
    if tool == "project_savings":
        return {"goal_name": ""}
    if tool == "compare_periods":
        # Default to this month vs last month
        first_this = today.replace(day=1)
        first_prev = (first_this - __import__("datetime").timedelta(days=1)).replace(day=1)
        # End = first of next month - 1 day
        def _end(d):
            nxt = (d.replace(day=28) + __import__("datetime").timedelta(days=4)).replace(day=1)
            return nxt - __import__("datetime").timedelta(days=1)
        return {
            "start_a": first_this.isoformat(), "end_a": _end(first_this).isoformat(),
            "start_b": first_prev.isoformat(), "end_b": _end(first_prev).isoformat(),
        }
    return {}
