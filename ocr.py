"""
ocr.py — Receipt scanning: Tesseract OCR + amount/merchant extraction +
category suggestion (learned ML classifier with keyword-map fallback).

OCR runs on the SERVER (the phone only uploads the photo), so any phone
works. When the Tesseract binary is missing, analyze_receipt reports
ok=False with reason="ocr_unavailable" and the UI shows a setup hint.
"""

import io
import re

_AMOUNT_RE = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|\d+\.\d{2}|\d+,\d{2})")
_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_TOTAL_KEYS = ("total", "ukupno", "suma", "svega", "amount due",
               "to pay", "grand total", "плати", "укупно")


def extract_amounts(text: str) -> list[float]:
    """Parse amounts in 1.234,56 / 1,234.56 / 1234.56 formats.

    Dates are stripped first so a receipt date like 15.05.2024 is never
    mistaken for an amount.
    """
    out = []
    if not text:
        return out
    cleaned = _DATE_RE.sub(" ", text)
    for m in _AMOUNT_RE.finditer(cleaned):
        raw = m.group()
        try:
            last_sep = max(raw.rfind("."), raw.rfind(","))
            if last_sep > 0 and len(raw) - last_sep - 1 == 2:
                intpart = raw[:last_sep].replace(".", "").replace(",", "")
                val = float(f"{intpart}.{raw[last_sep+1:]}")
            else:
                val = float(raw.replace(",", ""))
            if 0.01 <= val <= 1_000_000:
                out.append(val)
        except ValueError:
            continue
    return out


def guess_total_amount(text: str) -> float | None:
    """Best guess for the receipt total: an amount on a 'total' line, else
    the largest plausible amount."""
    amounts = extract_amounts(text)
    if not amounts:
        return None
    for line in text.splitlines():
        if any(k in line.lower() for k in _TOTAL_KEYS):
            line_amounts = extract_amounts(line)
            if line_amounts:
                return max(line_amounts)
    return max(amounts)


def guess_merchant(text: str) -> str | None:
    """First meaningful line that looks like a merchant name."""
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) < 3 or len(s) > 60:
            continue
        if extract_amounts(s):
            continue  # pure amount lines are not merchants
        if any(k in s.lower() for k in _TOTAL_KEYS):
            continue
        if re.fullmatch(r"[\d./\-:\s]+", s):
            continue  # dates / phone numbers / times
        return s
    return None


def ocr_image(image_bytes: bytes) -> str | None:
    """Run Tesseract on an image; None when it fails or isn't installed."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img)
        return text.strip() if text else None
    except Exception:
        return None


def analyze_receipt(image_bytes: bytes, expenses_df=None, user_id=None) -> dict:
    """Full pipeline: OCR → amount/merchant → category suggestion.

    Returns {"ok", "text", "amount", "merchant", "category", "subcategory",
    "confidence", "reason"}. Never raises; the UI turns this into an
    editable prefill that the user accepts/rejects.
    """
    text = ocr_image(image_bytes)
    if text is None:
        return {"ok": False, "reason": "ocr_unavailable",
                "text": None, "amount": None, "merchant": None,
                "category": None, "subcategory": "", "confidence": 0.0}

    amount = guess_total_amount(text)
    merchant = guess_merchant(text)
    category, subcategory, confidence = None, "", 0.0

    if merchant:
        # 1) learned classifier on the user's own data
        try:
            from forecasting import suggest_category
            cat, conf = suggest_category(expenses_df, merchant, user_id=user_id)
            if cat is not None:
                category, confidence = cat, round(conf, 2)
        except Exception:
            pass
        # 2) keyword-map fallback
        if category is None:
            from bank_import import categorize_expense
            category, subcategory = categorize_expense(merchant)

    return {"ok": True, "text": text, "amount": amount, "merchant": merchant,
            "category": category, "subcategory": subcategory,
            "confidence": confidence, "reason": None}
