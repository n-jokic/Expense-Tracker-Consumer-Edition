"""
services/mail_ingestion.py — paste-flow email staging (#26 E5).

Parses pasted order/shipping/subscription email text with the SAME extractors
the receipt pipeline uses (ocr.guess_total_amount, the line-item grammar,
bank_import's keyword category map) and produces structured candidates:

    {"description", "amount_eur", "category", "subcategory",
     "date", "confidence"}

Nothing books silently: the UI renders per-candidate Accept/Discard cards and
Accept routes through services.commands.add_expense (audited, undoable).
"""

from __future__ import annotations

import re
from datetime import date, datetime

_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),          # 2026-08-22
    re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b"),        # 22.08.2026
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),         # 8/22/2026 (US)
)

_ORDER_NOISE = ("order", "shipping", "delivery", "invoice", "total",
                "vat", "tax", "subtotal", "discount")


def _find_date(text: str) -> date | None:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            a, b, c = (int(m.group(i)) for i in (1, 2, 3))
            if pat is _DATE_PATTERNS[1]:
                return date(c, b, a)            # dd.mm.yyyy
            if pat is _DATE_PATTERNS[2]:
                return date(c, a, b)            # mm/dd/yyyy US order mails
            return date(a, b, c)
        except ValueError:
            continue
    return None


def _categorize(description: str) -> tuple[str, str]:
    try:
        from bank_import import categorize_expense
        cat, sub = categorize_expense(str(description))
        return str(cat or "Other"), str(sub or "")
    except Exception:
        return "Other", ""


def _confidence(item_amounts: int, has_date: bool,
                desc_len: int) -> float:
    score = 0.45
    if item_amounts:
        score += 0.25
    if has_date:
        score += 0.15
    if desc_len >= 3:
        score += 0.15
    return round(min(score, 1.0), 2)


def parse_email_text(text: str | None) -> list[dict]:
    """Turn pasted email text into booking candidates (never writes)."""
    raw = str(text or "").strip()
    if not raw:
        return []
    from ocr import guess_total_amount
    from ingestion.receipt.line_item_extractor import extract_line_items

    found_date = _find_date(raw)
    candidates: list[dict] = []

    # 1) structured product rows via the receipt grammar
    try:
        items = extract_line_items(raw) or []
    except Exception:
        items = []
    low_total = None
    try:
        low_total = guess_total_amount(raw)
    except Exception:
        low_total = None

    for it in items:
        desc = str(it.get("description") or "").strip()
        amt = it.get("line_total")
        if not desc or amt is None:
            continue
        try:
            amt_f = round(float(str(amt).replace(",", ".")), 2)
        except (TypeError, ValueError):
            continue
        if amt_f <= 0:
            continue
        cat, sub = _categorize(desc)
        candidates.append({
            "description": desc[:80],
            "amount_eur": amt_f,
            "category": cat,
            "subcategory": sub,
            "date": found_date,
            "confidence": _confidence(1, found_date is not None, len(desc)),
        })

    # 2) fallback: no item rows parsed but a total exists -> one candidate
    if not candidates and low_total and low_total > 0:
        first_lines = [ln.strip() for ln in raw.splitlines()
                       if ln.strip() and not any(k in ln.lower()
                                                 for k in _ORDER_NOISE)]
        subject = next((ln for ln in first_lines if len(ln) >= 3),
                       "Email order")
        cat, sub = _categorize(subject)
        candidates.append({
            "description": subject[:80],
            "amount_eur": round(float(low_total), 2),
            "category": cat,
            "subcategory": sub,
            "date": found_date,
            "confidence": _confidence(0, found_date is not None,
                                      len(subject)),
        })

    # dedupe identical description+amount rows; newest parse wins
    seen: set[tuple] = set()
    out: list[dict] = []
    for c in candidates:
        key = (str(c["description"]).lower(), round(float(c["amount_eur"]), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
