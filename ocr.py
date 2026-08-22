"""
ocr.py — Receipt scanning compatibility façade (Phase 5 O12).

New pipeline: ingestion.receipt.service.analyze_receipt
This module re-exports that implementation while preserving the legacy
Tesseract helpers (ocr_image, extract_amounts, guess_total_amount,
guess_merchant) for tests and existing imports.
"""

import io
import os
import re
import shutil
import threading

# Amounts: decimal forms (12,50 / 1.234,56 / 1,234.56) AND pure thousands
# groups (Serbian "1.234" = 1234). Bare integers (quantities, times) do not
# match.
_AMOUNT_RE = re.compile(
    r"(?<![\d.,])(?:"
    r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?"   # 1.234  or  1.234,56
    r"|\d{1,3}(?:[.,]\d{3})*[.,]\d{2}"       # 12,50  or  1.234,56
    r"|\d+[.,]\d{2}"                          # 1234,56
    r")(?![\d.,])"
)
_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_TOTAL_KEYS = ("total", "ukupno", "suma", "svega", "amount due",
               "to pay", "grand total", "плати", "укупно")


def _find_tesseract() -> str | None:
    r"""Locate the Tesseract binary on Windows."""
    exe = shutil.which("tesseract")
    if exe:
        return exe
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_path in (r"SOFTWARE\Tesseract-OCR",
                             r"SOFTWARE\WOW6432Node\Tesseract-OCR"):
                try:
                    with winreg.OpenKey(root, key_path) as k:
                        for value_name in ("InstallDir", "Path"):
                            try:
                                install_dir, _ = winreg.QueryValueEx(k, value_name)
                            except OSError:
                                continue
                            if install_dir:
                                candidates.append(
                                    os.path.join(str(install_dir), "tesseract.exe"))
                except OSError:
                    pass
    except Exception:
        pass
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


_OCR_TIMEOUT_S = 30


def ocr_image(image_bytes: bytes):
    """Run Tesseract on an image (worker thread with timeout)."""
    result: dict = {}

    def _run():
        try:
            import pytesseract
            tesseract = _find_tesseract()
            if not tesseract:
                result["text"], result["reason"] = None, "ocr_not_installed"
                return
            pytesseract.pytesseract.tesseract_cmd = tesseract
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(img)
            result["text"] = text.strip() if text else None
            result["reason"] = None
        except Exception:
            result["text"], result["reason"] = None, "ocr_failed"

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(_OCR_TIMEOUT_S)
    if worker.is_alive():
        return None, "ocr_failed"
    return result.get("text"), result.get("reason")


def extract_amounts(text: str) -> list[float]:
    """Parse amounts; dates stripped first."""
    from pdf_import import _parse_amount_core
    out = []
    if not text:
        return out
    cleaned = _DATE_RE.sub(" ", text)
    for m in _AMOUNT_RE.finditer(cleaned):
        val = _parse_amount_core(m.group())
        if val is not None and 0.01 <= val <= 1_000_000:
            out.append(val)
    return out


def guess_total_amount(text: str) -> float | None:
    """Best guess for receipt total: total-line else largest."""
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
            continue
        if any(k in s.lower() for k in _TOTAL_KEYS):
            continue
        if re.fullmatch(r"[\d./\-:\s]+", s):
            continue
        return s
    return None


def analyze_receipt(image_bytes: bytes, expenses_df=None, user_id=None,
                    user_locale=None, default_currency="EUR") -> dict:
    """Compatibility façade — delegates to ingestion.receipt.service."""
    try:
        from ingestion.receipt.service import analyze_receipt as _svc
        return _svc(image_bytes, expenses_df=expenses_df, user_id=user_id,
                    user_locale=user_locale, default_currency=default_currency)
    except Exception:
        # Fallback to legacy inline (should not happen; keep for safety)
        text, ocr_reason = ocr_image(image_bytes)
        if text is None:
            return {"ok": False, "reason": ocr_reason or "ocr_unavailable",
                    "text": None, "amount": None, "merchant": None,
                    "category": None, "subcategory": "", "confidence": 0.0,
                    "subcategory_confidence": None, "subcategory_source": None}
        amount = guess_total_amount(text)
        merchant = guess_merchant(text)
        category, subcategory, confidence = None, "", 0.0
        subcategory_confidence = None
        subcategory_source = None
        source = None
        model_version = None
        if merchant:
            try:
                from forecasting import (
                    suggest_category_and_subcategory, CATEGORIZER_MODEL_VERSION,
                    CATEGORY_CONFIDENCE, SUBCATEGORY_CONFIDENCE,
                )
                cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(
                    expenses_df, merchant, user_id=user_id)
                if cat_conf >= CATEGORY_CONFIDENCE:
                    category, subcategory, confidence = cat, sub, round(cat_conf, 2)
                    source = "classifier"
                    model_version = CATEGORIZER_MODEL_VERSION
                    if sub and sub_conf >= SUBCATEGORY_CONFIDENCE:
                        subcategory_confidence = round(sub_conf, 2)
                        subcategory_source = "classifier"
                    elif sub:
                        subcategory_source = "keywords"
            except Exception:
                pass
            if category is None:
                try:
                    from bank_import import categorize_expense
                    category, subcategory = categorize_expense(merchant)
                    source = "keywords"
                    subcategory_source = "keywords"
                except Exception:
                    pass
        return {"ok": True, "text": text, "amount": amount, "merchant": merchant,
                "category": category, "subcategory": subcategory,
                "confidence": confidence, "reason": None,
                "source": source, "model_version": model_version,
                "subcategory_confidence": subcategory_confidence,
                "subcategory_source": subcategory_source}
