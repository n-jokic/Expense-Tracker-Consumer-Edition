"""
ingestion/receipt/rapidocr_engine.py — RapidOCR 3.9.2 wrapper (O3).

Provides run_rapidocr(PIL Image) -> OCRDocument with tokens + confidences.
Gracefully returns unavailable OCRDocument when rapidocr/onnxruntime not installed.
"""

from __future__ import annotations

from typing import Any

OCR_ENGINE_VERSION = "rapidocr-3.9.2+pp-ocrv6-small"
_RAPID_CACHE = None  # lazy singleton


def _get_rapid_engine():
    global _RAPID_CACHE
    if _RAPID_CACHE is not None:
        return _RAPID_CACHE
    try:
        from rapidocr import RapidOCR  # type: ignore
        _RAPID_CACHE = RapidOCR()
        return _RAPID_CACHE
    except Exception:
        _RAPID_CACHE = None
        return None


def run_rapidocr(image, stage: str = "pass1"):
    """Run RapidOCR on a PIL Image -> OCRDocument.

    When rapidocr not installed, returns OCRDocument with engine='unavailable' and mean_confidence 0.
    """
    from ingestion.receipt.models import OCRDocument, OCRToken
    w, h = (image.size if hasattr(image, "size") else (0, 0))
    engine = _get_rapid_engine()
    if engine is None:
        return OCRDocument(tokens=[], width=int(w), height=int(h), mean_confidence=0.0,
                           engine="unavailable", model_version="none", preprocessing=stage)
    try:
        import numpy as np
        arr = np.array(image.convert("RGB"))
        # rapidocr returns list of [bbox, text, confidence] per line/box
        result = engine(arr)
        tokens: list[OCRToken] = []
        confs: list[float] = []
        line_id = 0
        # Handle different rapidocr return shapes: [boxes, texts, scores] or list of tuples
        # rapidocr 3.x returns (rec_res, elapse) where rec_res is list of [box, text, score]
        rec = result
        if isinstance(result, tuple) and len(result) == 2:
            rec = result[0]
        if rec:
            for item in rec:
                try:
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        box, text, score = item[0], item[1], item[2]
                    elif isinstance(item, dict):
                        box = item.get("box") or item.get("bbox") or []
                        text = item.get("text") or ""
                        score = float(item.get("score") or item.get("confidence") or 0)
                    else:
                        continue
                    text_s = str(text).strip()
                    if not text_s:
                        continue
                    conf = float(score) if score is not None else 0.0
                    confs.append(conf)
                    # polygon
                    try:
                        poly = tuple(tuple(float(c) for c in pt) for pt in box) if box else ((0,0),(0,0),(0,0),(0,0))
                    except Exception:
                        poly = ((0,0),(0,0),(0,0),(0,0))
                    tokens.append(OCRToken(text=text_s, confidence=conf, polygon=poly, line_id=line_id))
                    line_id += 1
                except Exception:
                    continue
        mean_conf = float(sum(confs) / len(confs)) if confs else 0.0
        return OCRDocument(tokens=tokens, width=int(w), height=int(h), mean_confidence=mean_conf,
                           engine="RapidOCR", model_version=OCR_ENGINE_VERSION, preprocessing=stage)
    except Exception:
        return OCRDocument(tokens=[], width=int(w), height=int(h), mean_confidence=0.0,
                           engine="RapidOCR-error", model_version=OCR_ENGINE_VERSION, preprocessing=stage)
