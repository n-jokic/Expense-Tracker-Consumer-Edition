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
    (r"improve.*financ|financial.*improve|doing financially|help.*financ", "__coach__"),
    (r"can i afford|afford.*(?:€|eur|euro)|purchase", "purchase_scenario"),
    (r"compare|higher.*than|more expensive|spending.*vs|vs.*spending", "compare_periods"),
    (r"top.*merchant|merchant.*breakdown|where.*shop", "merchant_breakdown"),
    (r"category.*breakdown|biggest expense category|top spending categor", "category_breakdown"),
    (r"search.*(?:purchase|transaction)|spend at|spent at|how much at", "search_transactions"),
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

_MONTHS = {
    name: index for index, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"), start=1
    )
}

def _extract_category(question: str) -> str | None:
    q = question.lower()
    for cat in _CATEGORY_HINTS:
        if cat in q:
            # Return canonical capitalised form
            return {
                "healthcare": "Health",
                "utilities": "Housing & Utilities",
                "food & dining": "Food & Dining",
            }.get(cat, cat.title())
    return None


def _extract_month(question: str, today: date) -> tuple[int, int]:
    """Return the month named by the question, defaulting to the current month."""
    q = question.lower()
    explicit = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(20\d{2})\b", q)
    if explicit:
        return int(explicit.group(2)), _MONTHS[explicit.group(1)]
    for name, month in _MONTHS.items():
        if re.search(rf"\b{name}\b", q):
            return today.year, month
    if "last month" in q or "previous month" in q:
        return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return today.year, today.month


def fast_route(question: str) -> str | None:
    """Return a tool name for deterministic fast-path, or None to use planner."""
    q = question.lower()
    for pat, tool in DETERMINISTIC_PATTERNS:
        if re.search(pat, q):
            return tool
    return None


# Tools whose arguments include a {year, month} period pair.
_YEAR_MONTH_TOOLS = frozenset({
    "aggregate_spending", "category_breakdown", "merchant_breakdown",
    "cashflow_summary", "budget_status", "purchase_scenario",
})

_INT_FIELDS = ("year", "month", "n", "limit", "term_months")


def repair_missing_dates(tool: str, arguments: dict | None, question: str,
                         today: date | None = None) -> tuple[dict, bool]:
    """AI-02: deterministic argument repair BEFORE any model repair round.

    Fills missing ``year``/``month`` from the original question (explicit
    month, month name, or "last month" all resolve correctly via
    ``_extract_month``; no month named means the current month) and coerces
    numeric-string arguments to numbers so type validation passes without a
    second model call.

    Returns ``(arguments, ambiguous)`` — ``ambiguous`` is True when the
    question names two or more DIFFERENT months and the caller should ask
    for clarification instead of guessing.
    """
    today = today or date.today()
    args = dict(arguments or {})

    # Numeric coercion first: models often emit "2025" instead of 2025.
    for k in _INT_FIELDS:
        v = args.get(k)
        if isinstance(v, str):
            try:
                args[k] = int(v.strip())
            except (ValueError, TypeError):
                match = re.match(r"^\s*(\d+)", v)
                if match:
                    args[k] = int(match.group(1))
    for k in ("total_budget_eur", "principal_eur", "annual_rate_pct",
              "extra_monthly_eur", "purchase_eur", "multiplier"):
        v = args.get(k)
        if isinstance(v, str):
            try:
                args[k] = float(v.replace(",", "").strip())
            except (ValueError, TypeError):
                pass

    # Period ambiguity check on the raw question (distinct month names).
    months_named = {_MONTHS[m] for m in _MONTHS if re.search(rf"\b{m}\b", question.lower())}
    ambiguous = len(months_named) > 1

    if tool in _YEAR_MONTH_TOOLS:
        year, month = _extract_month(question, today)
        if "year" not in args:
            args["year"] = year
        if "month" not in args:
            args["month"] = month
    return args, ambiguous


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
        if k in ("total_budget_eur", "principal_eur", "annual_rate_pct", "extra_monthly_eur", "purchase_eur", "multiplier"):
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
        year, month = _extract_month(question, today)
        args: dict[str, Any] = {"year": year, "month": month}
        if cat:
            args["category"] = cat
        return args
    if tool == "budget_status":
        return {"year": today.year, "month": today.month}
    if tool == "budget_runway":
        return {"period_start": today.replace(day=1).isoformat()}
    if tool == "category_breakdown":
        year, month = _extract_month(question, today)
        return {"year": year, "month": month}
    if tool == "merchant_breakdown":
        year, month = _extract_month(question, today)
        return {"year": year, "month": month, "n": 5}
    if tool == "cashflow_summary":
        year, month = _extract_month(question, today)
        return {"year": year, "month": month}
    if tool == "search_transactions":
        match = re.search(r"(?:at|for)\s+['\"]?([\w .&-]+?)(?:['\"]?\s+(?:this|last|in)\b|[?!.]?$)", question, re.I)
        return {"query": (match.group(1) if match else question).strip(), "limit": 20}
    if tool == "purchase_scenario":
        amount = re.search(r"(?:€|eur\s*|euro\s*)([\d.,]+)|([\d.,]+)\s*(?:€|eur|euro)", question, re.I)
        if amount is None:
            return {"purchase_eur": 0, "year": today.year, "month": today.month}
        value = (amount.group(1) or amount.group(2)).replace(",", "")
        year, month = _extract_month(question, today)
        return {"purchase_eur": float(value), "year": year, "month": month}
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
