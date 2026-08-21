"""
ingestion/receipt/line_item_extractor.py — line-item extraction and total
reconciliation (OCR-01).

Turns receipt OCR text into structured rows (description, quantity,
unit_price, line_total) instead of only a grand total, then reconciles the
item sum against the stated total within EUR_TOLERANCE = €0.01. Mismatches
are REPORTED (delta, ok=False), never silently accepted or clamped.
"""

from __future__ import annotations

import re

EUR_TOLERANCE = 0.01

# Lines that are never products: totals, payment, meta.
_EXCLUDE_KEYS = (
    "total", "ukupno", "suma", "svega", "subtotal", "medjuzbir", "međuzbir",
    "meduzbir", "pdv", "vat", "tax", "gotovina", "cash", "kartica", "card",
    "kusur", "change", "povrat", "rest", "vraceno", "amount due", "to pay",
    "grand total", "datum", "date", "racun", "račun", "ppdv", "sifra",
    "šifra", "hvala", "thank", "adresa", "address", "pib", "vati",
)

_QTY_PREFIX_RE = re.compile(r"^\s*(\d{1,3})\s*[xX×*]\s*")
_DATE_LINE_RE = re.compile(r"^\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")

# Amount pattern reused from the legacy façade so parsing stays in sync;
# wrapped with an optional leading/trailing minus for discount lines.
from ocr import _AMOUNT_RE as _AMT_CORE  # noqa: E402

_SIGNED_AMT_RE = re.compile(
    r"(?P<lead>-)?\s*(?P<amt>" + _AMT_CORE.pattern + r")\s*(?P<trail>-)?")


def _signed_amounts(line: str) -> list[tuple[float, int]]:
    """[(value, start_index)] — dates stripped first, discounts signed."""
    from ocr import _DATE_RE

    cleaned = _DATE_RE.sub(" ", _TIME_RE.sub(" ", line))
    out: list[tuple[float, int]] = []
    for m in _SIGNED_AMT_RE.finditer(cleaned):
        raw = m.group("amt")
        try:
            from pdf_import import _parse_amount_core
            val = _parse_amount_core(raw)
        except Exception:
            val = None
        if val is None:
            continue
        val = float(val)
        if m.group("lead") or m.group("trail"):
            val = -abs(val)
        out.append((val, m.start("amt")))
    # de-duplicate overlaps (lookarounds already prevent most)
    deduped: list[tuple[float, int]] = []
    seen_spans: list[tuple[int, int]] = []
    for val, start in out:
        span = (start, start + len(str(abs(val))))
        if any(not (span[1] < s or span[0] > e) for s, e in seen_spans):
            continue
        seen_spans.append(span)
        deduped.append((val, start))
    return deduped


def _clean_description(text: str) -> str:
    text = _QTY_PREFIX_RE.sub("", text)
    text = re.sub(r"[\s\-–•*·.,;:]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_line_items(raw_text: str | None = None,
                       document=None) -> list[dict]:
    """Extract product rows from receipt OCR text.

    Accepts raw OCR text directly (preferred) or an OCRDocument whose tokens
    are grouped by line_id. Returns a list of
    ``{"description", "quantity", "unit_price", "line_total"}``.
    """
    if not raw_text and document is not None and getattr(document, "tokens", None):
        by_line: dict[int, list[str]] = {}
        for tok in document.tokens:
            by_line.setdefault(tok.line_id, []).append(tok.text)
        raw_text = "\n".join(" ".join(parts) for _, parts in sorted(by_line.items()))
    if not raw_text:
        return []

    items: list[dict] = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s or len(s) < 3:
            continue
        low = s.lower()
        if any(k in low for k in _EXCLUDE_KEYS):
            continue
        if _DATE_LINE_RE.match(s) or _TIME_RE.search(s) and not _signed_amounts(s):
            continue
        amounts = _signed_amounts(s)
        if not amounts:
            continue
        # The LAST amount on a product row is its line total.
        line_total, last_start = amounts[-1]
        desc_slice = s[:last_start]
        # Quantity multiplier may sit mid-row: "Mleko 2 x 289,99".
        qty_match = _QTY_PREFIX_RE.match(desc_slice)
        if qty_match:
            description = _clean_description(desc_slice[qty_match.end():])
            quantity = int(qty_match.group(1))
        else:
            inline = re.search(
                r"^(?P<desc>.*?)\s+(?P<qty>\d{1,3})\s*[xX×*]\s+"
                r"(?:\d[\d.,]*\s*)?$",
                desc_slice)
            if inline:
                description = _clean_description(inline.group("desc"))
                quantity = int(inline.group("qty"))
            else:
                description = _clean_description(desc_slice)
                quantity = 1
        if not description:
            # e.g. bare amount row — keep it only when it plausibly is a
            # single purchase line without a name; skip otherwise.
            continue
        quantity = max(1, min(quantity, 999))
        if quantity > 1 and len(amounts) >= 2:
            unit_price = round(float(amounts[-2][0]), 2)
        elif quantity > 1:
            unit_price = round(line_total / quantity, 2)
        else:
            unit_price = round(float(line_total), 2)
        items.append({
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": round(float(line_total), 2),
        })
    return items


def reconcile(items: list[dict], stated_total: float | None) -> dict:
    """Sum item rows against the stated grand total.

    Returns {"items_total", "stated_total", "delta", "ok"} where ok holds
    within EUR_TOLERANCE (€0.01). Never clamps or silently accepts."""
    items_total = round(sum(float(i["line_total"]) for i in items), 2)
    stated = float(stated_total) if stated_total is not None else None
    delta = None if stated is None else round(stated - items_total, 2)
    return {
        "items_total": items_total,
        "stated_total": stated,
        "delta": delta,
        "ok": delta is not None and abs(delta) <= EUR_TOLERANCE,
    }
