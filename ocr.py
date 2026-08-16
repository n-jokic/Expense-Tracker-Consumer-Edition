"""
ocr.py — Receipt scanning: Tesseract OCR + amount/merchant extraction +
category suggestion (learned ML classifier with keyword-map fallback).

OCR runs on the SERVER (the phone only uploads the photo), so any phone
works. When the Tesseract binary is missing, analyze_receipt reports
ok=False with reason="ocr_unavailable" and the UI shows a setup hint.
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
    r"""Locate the Tesseract binary on Windows.

    `winget install UB-Mannheim.TesseractOCR` installs into
    `C:\Program Files\Tesseract-OCR` and writes a registry key, but does NOT
    add the folder to PATH — so a plain PATH lookup (pytesseract's default)
    keeps failing after install. Resolve: PATH first, then the common
    install locations, then the registry InstallDir (incl. WOW6432Node for
    32-bit installs and the alternative `Path` value name).
    """
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
    """Run Tesseract on an image (in a worker thread with a timeout, so a
    hung OCR run can never freeze the app indefinitely).

    Returns (text, reason): text is the recognised string (or None), and
    reason explains a failure — "ocr_not_installed" when the Tesseract binary
    can't be found, "ocr_failed" on any other error or timeout, None on
    success.
    """
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
        return None, "ocr_failed"   # timed out; daemon thread dies with the process
    return result.get("text"), result.get("reason")


def extract_amounts(text: str) -> list[float]:
    """Parse amounts in 1.234,56 / 1,234.56 / 1234.56 / 12,50 formats and
    Serbian pure-thousands "1.234" (= 1234).

    Dates are stripped first so a receipt date like 15.05.2024 is never
    mistaken for an amount.
    """
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


def analyze_receipt(image_bytes: bytes, expenses_df=None, user_id=None) -> dict:
    """Full pipeline: OCR → amount/merchant → category suggestion.

    Returns {"ok", "text", "amount", "merchant", "category", "subcategory",
    "confidence", "subcategory_confidence", "subcategory_source", "reason"}.
    Never raises; the UI turns this into an editable prefill that the user
    accepts/rejects.
    """
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
        # 1) learned classifier (+ per-category subcategorizer)
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
        # 2) keyword-map fallback
        if category is None:
            from bank_import import categorize_expense
            category, subcategory = categorize_expense(merchant)
            source = "keywords"
            subcategory_source = "keywords"

    return {"ok": True, "text": text, "amount": amount, "merchant": merchant,
            "category": category, "subcategory": subcategory,
            "confidence": confidence, "reason": None,
            "source": source, "model_version": model_version,
            "subcategory_confidence": subcategory_confidence,
            "subcategory_source": subcategory_source}
