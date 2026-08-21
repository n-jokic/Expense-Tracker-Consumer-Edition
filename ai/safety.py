"""
ai/safety.py — advisor safety boundary (Phase 3 A7).

READ-ONLY initially. No direct SQL. No direct service mutation.
Proposed mutations must be confirmed via UI confirmation button that calls
the command service directly — the model never executes them.
"""

from __future__ import annotations

import re
from typing import Any

# Tools are read-only; this set is intentionally empty until explicit
# user-confirmed mutations are introduced.
ALLOWED_MUTATIONS: set[str] = set()

# Patterns that must never appear in model output that claims to be a tool call
# (prevents prompt-injection trying to exfiltrate or mutate).
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"DROP\s+TABLE", re.I),
    re.compile(r"DELETE\s+FROM", re.I),
    re.compile(r"UPDATE\s+\w+\s+SET", re.I),
    re.compile(r"INSERT\s+INTO", re.I),
    re.compile(r";\s*--"),
]

# Maximum argument string length to prevent prompt injection via huge payloads
MAX_ARGUMENT_VALUE_LEN = 500


def is_read_only_tool(tool: str) -> bool:
    """True if tool is in read-only allowlist."""
    try:
        from ai.tool_registry import TOOLS
        return tool in TOOLS
    except Exception:
        return False


def sanitize_question(question: str) -> str:
    """Strip newlines, cap length, neutralize instruction-injection patterns."""
    if not question:
        return ""
    # Collapse newlines so stored/hostile strings cannot inject instructions
    s = str(question).replace("\r", " ").replace("\n", " ")
    # Cap to 500 chars — longer questions are truncated, not rejected
    if len(s) > MAX_ARGUMENT_VALUE_LEN:
        s = s[:MAX_ARGUMENT_VALUE_LEN]
    return s.strip()


def validate_no_sql(text: str) -> tuple[bool, str | None]:
    """Check tool output for SQL injection patterns."""
    if not text:
        return True, None
    for pat in _BLOCKED_PATTERNS:
        if pat.search(text):
            return False, f"blocked pattern: {pat.pattern}"
    return True, None


def check_mutation_proposal(question: str) -> dict | None:
    """Detect if user is asking for a mutation (e.g. 'Set my Dining budget to €350').

    Returns a proposal dict with type/args for the UI confirm button, or None.
    The model never executes it — only the confirmation button calls
    budget_commands.set_budget etc. (A7 safety).
    """
    q = question.lower()
    # Budget change intent
    m = re.search(r"set.*budget.*?(\d+(?:\.\d+)?)", q)
    if m:
        amount = m.group(1)
        # Try to extract category
        cats = [
            "housing & utilities", "groceries", "dining out", "transport", "travel",
            "entertainment", "shopping", "subscriptions & software", "fees & taxes",
            "loans & debt", "health", "other",
        ]
        cat = None
        for c in cats:
            if c in q:
                cat = c.title() if c != "housing & utilities" else "Housing & Utilities"
                # Fix title casing for special categories
                if c == "subscriptions & software":
                    cat = "Subscriptions & Software"
                elif c == "fees & taxes":
                    cat = "Fees & Taxes"
                elif c == "loans & debt":
                    cat = "Loans & Debt"
                break
        return {
            "type": "budget_change",
            "category": cat,
            "amount_eur": float(amount),
            "proposed": True,
            "requires_confirmation": True,
            "message": f"Proposed change: set {cat or 'category'} budget to €{amount} — confirm to apply.",
        }
    return None


def tool_result_with_provenance_check(result: dict) -> bool:
    """Verify tool result carries _provenance (A3 gate)."""
    return isinstance(result, dict) and "_provenance" in result


_WHITESPACE_COLLAPSE = re.compile(r"\s+")

# Cap for individual sanitised string leaves in tool results.
MAX_UNSANITIZED_STR_LEN = 200
_MAX_SANITIZE_DEPTH = 6


def sanitize_untrusted_text(value: Any, max_len: int = MAX_UNSANITIZED_STR_LEN) -> str:
    """Make a tool-result string leaf safe for prompt embedding.

    Mirrors llm._sanitize_stat semantics exactly: newlines become spaces,
    internal whitespace runs are collapsed, and the result is hard-capped
    to max_len characters so stored data cannot inject instructions into
    the prompt."""
    if not isinstance(value, str):
        value = str(value)
    s = value.replace("\r", " ").replace("\n", " ")
    s = _WHITESPACE_COLLAPSE.sub(" ", s)
    if len(s) > max_len:
        s = s[:max_len]
    return s.strip()


def sanitize_tool_result(obj: Any, _depth: int = 0) -> Any:
    """Recursively sanitize untrusted tool results before prompt embedding.

    dict/list/tuple containers are preserved; every str leaf is sanitized via
    sanitize_untrusted_text; non-string leaves are returned untouched.
    Recursion is depth-capped to defend against pathological nesting."""
    if _depth > _MAX_SANITIZE_DEPTH:
        return obj
    if isinstance(obj, str):
        return sanitize_untrusted_text(obj)
    if isinstance(obj, dict):
        return {k: sanitize_tool_result(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_tool_result(v, _depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_tool_result(v, _depth + 1) for v in obj)
    return obj
