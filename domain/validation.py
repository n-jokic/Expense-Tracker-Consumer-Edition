"""
domain/validation.py — canonical validation (Streamlit-free).

Shared by Streamlit, bank import, OCR, MCP, sync, and future AI actions.
All finance arithmetic lives in services/; this module only validates inputs.
"""

from __future__ import annotations

import math

from domain.money import SUPPORTED_CURRENCIES, MAX_AMOUNT
from domain.taxonomy import CATEGORIES, CAT_LIST, INCOME_TYPES


def validate_category(category: str) -> str:
    cat = (category or "").strip()
    if cat not in CAT_LIST:
        raise ValueError(f"unknown category '{cat}' — use one of: {', '.join(CAT_LIST)}")
    return cat


def validate_category_subcategory(category: str, subcategory: str = "") -> tuple[str, str]:
    """Validate category + subcategory pair. Empty subcategory is always valid."""
    cat = validate_category(category)
    sub = (subcategory or "").strip()
    if sub and sub not in CATEGORIES[cat]:
        raise ValueError(f"unknown subcategory '{sub}' for {cat} "
                         f"(valid: {', '.join(CATEGORIES[cat])})")
    return cat, sub


def validate_category_in(category: str, cats_dict: dict[str, list]) -> str:
    """#16: taxonomy-aware variant — validates against the caller's effective
    registry (queries.effective_categories output) instead of the static map."""
    cat = (category or "").strip()
    if cat not in cats_dict:
        raise ValueError(f"unknown category '{cat}' — use one of: "
                         f"{', '.join(cats_dict)}")
    return cat


def validate_category_subcategory_in(category: str, subcategory: str,
                                     cats_dict: dict[str, list]) -> tuple[str, str]:
    """Taxonomy-aware pair validation; empty subcategory stays always valid."""
    cat = validate_category_in(category, cats_dict)
    sub = (subcategory or "").strip()
    if sub and sub not in (cats_dict.get(cat) or []):
        raise ValueError(f"unknown subcategory '{sub}' for {cat} "
                         f"(valid: {', '.join(cats_dict.get(cat) or [])})")
    return cat, sub


def map_unknown_category(text: str, cats_dict: dict[str, list]) -> tuple[str, str]:
    """#16 import-path fallback chain for rows whose stored category is no
    longer valid: exact match -> keyword rules -> 'Uncategorized' catch-all.
    Returns (category, subcategory)."""
    raw_cat = str(text or "").strip()
    if raw_cat in cats_dict:
        return raw_cat, ""
    try:
        from bank_import import categorize_expense
        kw_cat, kw_sub = categorize_expense(raw_cat)
    except Exception:
        kw_cat, kw_sub = None, ""
    if kw_cat and kw_cat in cats_dict:
        subs = cats_dict.get(kw_cat) or []
        if kw_sub and kw_sub in subs:
            return kw_cat, kw_sub
        return kw_cat, ""
    return "Uncategorized", ""


def validate_income_type(income_type: str) -> str:
    itype = (income_type or "Other").strip()
    if itype not in INCOME_TYPES:
        raise ValueError(f"unknown income_type '{itype}' — use one of: "
                         f"{', '.join(INCOME_TYPES)}")
    return itype


def validate_currency(currency: str) -> str:
    cur = (currency or "EUR").strip().upper()
    if cur not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unknown currency '{cur}'")
    return cur


def normalize_description(description: str, *, required: bool = True) -> str:
    desc = (description or "").strip()
    if required and not desc:
        raise ValueError("description is required")
    if len(desc) > 500:
        raise ValueError("description must be at most 500 characters")
    return desc


def validate_amount(amount, *, field: str = "amount", allow_zero: bool = False) -> float:
    """Validate a numeric amount; returns float(amount)."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValueError(f"{field} must be a number")
    amt = float(amount)
    if not math.isfinite(amt):
        raise ValueError(f"{field} must be a finite number")
    if amt < 0 or (not allow_zero and amt <= 0) or amt > MAX_AMOUNT:
        if allow_zero and amt == 0:
            pass
        elif amt <= 0:
            raise ValueError(f"{field} must be > 0 and <= {MAX_AMOUNT:g}")
        elif amt > MAX_AMOUNT:
            raise ValueError(f"{field} must be > 0 and <= {MAX_AMOUNT:g}")
    return amt


def normalize_currency(currency: str | None) -> str:
    """Alias for validate_currency with a nicer name for importers."""
    return validate_currency(currency or "EUR")


def normalize_amount(amount, field: str = "amount") -> float:
    """Alias for validate_amount with the common call-site name."""
    return validate_amount(amount, field=field)


def is_valid_amount(value) -> bool:
    """Predicate form of validate_amount for data-editor row filtering.

    Accepts only finite floats strictly greater than 0 and within the money
    cap (MAX_AMOUNT); everything else (None, strings, inf/NaN, zero,
    negatives, oversize) returns False so callers can surface per-row
    messaging instead of poisoning SQLite REAL columns and downstream sums.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0 and f <= MAX_AMOUNT
