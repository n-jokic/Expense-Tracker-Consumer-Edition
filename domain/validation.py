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
