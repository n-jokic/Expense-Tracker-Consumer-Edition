"""
ingestion/receipt/service.py — O2/O3/O9 orchestrator + O12 façade target.

Policy:
  RapidOCR pass1 -> extractors -> if mean_conf >=0.75 and total conf >=0.80 stop
  else pass2 (CLAHE-like: convert to grayscale, adaptive threshold via PIL) -> pick better by OCR+field confidence
  If RapidOCR unavailable, delegate to Tesseract fallback (ocr.ocr_image + regex extractors).
  Caches by sha256(image_bytes + ENGINE_VERSION + PREPROCESSING_VERSION) module-level.
  Returns dict shaped like ocr.analyze_receipt for compat plus `receipt_result`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ingestion.receipt.models import ReceiptResult, OCRDocument, FieldCandidate
from ingestion.receipt.preprocess import PREPROCESSING_VERSION, preprocess_image
from ingestion.receipt.rapidocr_engine import OCR_ENGINE_VERSION
from ingestion.receipt.total_extractor import extract_total_candidates
from ingestion.receipt.merchant_extractor import extract_merchant_candidates
from ingestion.receipt.date_extractor import extract_date_candidates
from ingestion.receipt.currency_extractor import extract_currency_candidates
from ingestion.receipt.confidence import score_receipt_result

_CACHE: dict[str, dict] = {}

OCR_ENGINE_KEY_VERSION = f"{OCR_ENGINE_VERSION}__{PREPROCESSING_VERSION}"

def _image_cache_key(image_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(OCR_ENGINE_KEY_VERSION.encode())
    return h.hexdigest()

def _best_total(cands: list[FieldCandidate]):
    return cands[0] if cands else None

def _build_receipt_result(document: OCRDocument, raw_text: str) -> ReceiptResult:
    total_cands = extract_total_candidates(document, raw_text)
    merch_cands = extract_merchant_candidates(document, raw_text)
    date_cands = extract_date_candidates(document, raw_text)
    curr_cands = extract_currency_candidates(document, raw_text)
    result = ReceiptResult(
        merchant=merch_cands[0] if merch_cands else None,
        total=total_cands[0] if total_cands else None,
        date=date_cands[0] if date_cands else None,
        currency=curr_cands[0] if curr_cands else None,
        alternatives={
            "total": total_cands[1:4] if len(total_cands) > 1 else [],
            "merchant": merch_cands[1:3] if len(merch_cands) > 1 else [],
            "date": date_cands[1:3] if len(date_cands) > 1 else [],
            "currency": curr_cands[1:3] if len(curr_cands) > 1 else [],
        },
        document=document,
    )
    return score_receipt_result(result)

def _tesseract_fallback(image_bytes: bytes):
    """Use existing Tesseract pipeline when RapidOCR unavailable."""
    import ocr as _ocr
    text, reason = _ocr.ocr_image(image_bytes)
    if text is None:
        doc = OCRDocument(engine="tesseract-unavailable", mean_confidence=0.0)
        result = ReceiptResult(document=doc)
        return doc, text or "", result, reason
    # Build pseudo OCRDocument from text lines with heuristic confidences
    from ingestion.receipt.models import OCRToken
    tokens: list[OCRToken] = []
    for idx, line in enumerate(text.splitlines()):
        if line.strip():
            tokens.append(OCRToken(text=line.strip(), confidence=0.85, polygon=((0,0),(0,0),(0,0),(0,0)), line_id=idx))
    doc = OCRDocument(tokens=tokens, width=0, height=0, mean_confidence=0.6, engine="tesseract", model_version="tesseract", preprocessing="tesseract")
    result = _build_receipt_result(doc, text)
    return doc, text, result, None

def analyze_receipt(image_bytes: bytes, expenses_df=None, user_id=None) -> dict:
    """Full pipeline compatible with ocr.analyze_receipt return shape."""
    # Cache
    try:
        key = _image_cache_key(image_bytes)
        if key in _CACHE:
            cached = _CACHE[key]
            # still re-run category suggestion with current expenses_df
            return _with_category_suggestion(cached, expenses_df, user_id)
    except Exception:
        key = None

    # RapidOCR path
    try:
        from ingestion.receipt.rapidocr_engine import run_rapidocr
        from PIL import Image as PILImage
        # pass1
        img1 = preprocess_image(image_bytes, stage="pass1")
        doc1 = run_rapidocr(img1, stage="pass1")
        # If engine unavailable, fallback
        if doc1.engine == "unavailable" or doc1.mean_confidence == 0 and not doc1.tokens:
            doc, raw_text, receipt_result, reason = _tesseract_fallback(image_bytes)
            total_conf = receipt_result.total.confidence if receipt_result.total else 0
            # Build compat dict
            compat = _to_compat_dict(doc, raw_text, receipt_result, reason, expenses_df, user_id)
            if key:
                _CACHE[key] = compat
            return compat
        raw_text1 = "\n".join(" ".join(t.text for t in [tok for tok in doc1.tokens if tok.line_id==lid]) for lid in sorted({t.line_id for t in doc1.tokens})) if doc1.tokens else ""
        if not raw_text1 and doc1.tokens:
            raw_text1 = "\n".join(t.text for t in doc1.tokens)
        result1 = _build_receipt_result(doc1, raw_text1)
        total_conf1 = result1.total.confidence if result1.total else 0
        use_pass2 = not (doc1.mean_confidence >= 0.75 and total_conf1 >= 0.80)
        if not use_pass2:
            compat = _to_compat_dict(doc1, raw_text1, result1, None, expenses_df, user_id)
            if key:
                _CACHE[key] = compat
            return compat
        # pass2: light grayscale path (simulate CLAHE via convert L + optional autocontrast)
        try:
            from PIL import ImageOps, Image
            img2_raw = preprocess_image(image_bytes, stage="pass2")
            # Grayscale + autocontrast as light CLAHE substitute
            gray = img2_raw.convert("L")
            gray = ImageOps.autocontrast(gray, cutoff=2)
            # back to RGB for RapidOCR
            img2 = gray.convert("RGB")
            doc2 = run_rapidocr(img2, stage="pass2")
            raw_text2 = "\n".join(t.text for t in doc2.tokens) if doc2.tokens else ""
            result2 = _build_receipt_result(doc2, raw_text2) if raw_text2 else result1
            # pick better by OCR conf + field conf
            score1 = doc1.mean_confidence + (total_conf1 or 0)
            total_conf2 = result2.total.confidence if result2.total else 0
            score2 = doc2.mean_confidence + (total_conf2 or 0)
            if score2 > score1:
                compat = _to_compat_dict(doc2, raw_text2, result2, None, expenses_df, user_id)
            else:
                compat = _to_compat_dict(doc1, raw_text1, result1, None, expenses_df, user_id)
            if key:
                _CACHE[key] = compat
            return compat
        except Exception:
            compat = _to_compat_dict(doc1, raw_text1, result1, None, expenses_df, user_id)
            if key:
                _CACHE[key] = compat
            return compat
    except Exception as e:
        # Any failure -> tesseract fallback
        try:
            doc, raw_text, receipt_result, reason = _tesseract_fallback(image_bytes)
            compat = _to_compat_dict(doc, raw_text, receipt_result, reason, expenses_df, user_id)
            if key:
                _CACHE[key] = compat
            return compat
        except Exception:
            return {"ok": False, "reason": "ocr_failed", "text": None, "amount": None, "merchant": None,
                    "category": None, "subcategory": "", "confidence": 0.0,
                    "subcategory_confidence": None, "subcategory_source": None,
                    "receipt_result": ReceiptResult(document=OCRDocument(engine="error"))}

def _with_category_suggestion(compat: dict, expenses_df, user_id) -> dict:
    """Re-attach category suggestion with current df (cache reuse)."""
    try:
        merchant = compat.get("merchant")
        if not merchant:
            return compat
        # Re-run suggestion if previously unknown
        from forecasting import suggest_category_and_subcategory, CATEGORIZER_MODEL_VERSION, CATEGORY_CONFIDENCE, SUBCATEGORY_CONFIDENCE
        cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(expenses_df, merchant, user_id=user_id)
        if cat_conf >= CATEGORY_CONFIDENCE:
            compat = dict(compat)
            compat["category"] = cat
            compat["subcategory"] = sub
            compat["confidence"] = round(cat_conf, 2)
            compat["model_version"] = CATEGORIZER_MODEL_VERSION
            if sub and sub_conf >= SUBCATEGORY_CONFIDENCE:
                compat["subcategory_confidence"] = round(sub_conf, 2)
                compat["subcategory_source"] = "classifier"
            elif sub:
                compat["subcategory_source"] = "keywords"
            return compat
    except Exception:
        pass
    return compat

def _to_compat_dict(document: OCRDocument, raw_text: str, receipt_result: ReceiptResult, reason, expenses_df, user_id) -> dict:
    """Map ReceiptResult -> legacy analyze_receipt dict."""
    amount = float(receipt_result.total.value) if receipt_result.total and receipt_result.total.value is not None else None
    merchant = str(receipt_result.merchant.value) if receipt_result.merchant and receipt_result.merchant.value else None
    # Fallback merchant guess from raw_text if merchant extractor missed
    if not merchant and raw_text:
        try:
            from ocr import guess_merchant as _gm
            merchant = _gm(raw_text)
        except Exception:
            pass
    if amount is None and raw_text:
        try:
            from ocr import guess_total_amount as _gta
            alt = _gta(raw_text)
            if alt is not None:
                amount = float(alt)
        except Exception:
            pass
    # Category suggestion
    category, subcategory, confidence = None, "", 0.0
    subcategory_confidence = None
    subcategory_source = None
    source = None
    model_version = None
    if merchant:
        try:
            from forecasting import suggest_category_and_subcategory, CATEGORIZER_MODEL_VERSION, CATEGORY_CONFIDENCE, SUBCATEGORY_CONFIDENCE
            cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(expenses_df, merchant, user_id=user_id)
            if cat_conf >= CATEGORY_CONFIDENCE:
                category, subcategory, confidence = cat, sub, round(cat_conf, 2)
                source = "classifier"
                model_version = CATEGORIZER_MODEL_VERSION
                if sub and sub_conf >= SUBCATEGORY_CONFIDENCE:
                    subcategory_confidence = round(sub_conf, 2)
                    subcategory_source = "classifier"
                elif sub:
                    subcategory_source = "keywords"
            else:
                raise ValueError("low confidence")
        except Exception:
            try:
                from bank_import import categorize_expense
                category, subcategory = categorize_expense(merchant)
                source = "keywords"
                subcategory_source = "keywords"
            except Exception:
                pass
    ok = receipt_result.total is not None or merchant is not None or bool(raw_text)
    if document.engine in ("tesseract-unavailable",) and not raw_text:
        ok = False
        reason = reason or "ocr_not_installed"
    return {
        "ok": bool(ok),
        "text": raw_text,
        "amount": amount,
        "merchant": merchant,
        "category": category,
        "subcategory": subcategory,
        "confidence": confidence,
        "reason": reason,
        "source": source,
        "model_version": model_version,
        "subcategory_confidence": subcategory_confidence,
        "subcategory_source": subcategory_source,
        "receipt_result": receipt_result,
        "document": document,
    }
