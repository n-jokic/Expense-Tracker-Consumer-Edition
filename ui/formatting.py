"""
ui/formatting.py — display formatting helpers (Streamlit-free except for fmt
helpers that are pure functions).
"""

from __future__ import annotations

from domain.money import get_currency_symbol, to_display, to_display_row


def _fmt_number(v: float, currency: str) -> str:
    sym = get_currency_symbol(currency)
    if currency in ("RSD", "HUF", "HRK"):
        return f"{v:,.0f} {sym}"
    return f"{sym}{v:,.2f}"


def fmt(eur: float, currency: str, rates: dict) -> str:
    return _fmt_number(to_display(eur, currency, rates), currency)


def fmt_row(eur: float, orig_amount: float, orig_currency: str,
            currency: str, rates: dict) -> str:
    return _fmt_number(to_display_row(eur, orig_amount, orig_currency, currency, rates),
                       currency)


def fmt_dual(orig_amount: float, orig_currency: str, eur: float) -> str:
    if orig_currency == "EUR":
        return f"€{eur:,.2f}"
    return f"{_fmt_number(float(orig_amount), orig_currency)} / €{eur:,.2f}"
