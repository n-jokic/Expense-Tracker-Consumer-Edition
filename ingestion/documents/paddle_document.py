"""ingestion/documents/paddle_document.py — heavy document parsers (PP-StructureV3 / PaddleOCR-VL-1.6).

Stubs that degrade gracefully when Paddle deps not installed. Heavy stack is
requirements-ocr-advanced.txt only; desktop remains functional without it.
"""

from __future__ import annotations

def parse_complex_document(image_bytes: bytes) -> dict:
    """Parse complex table/layout via PP-StructureV3 or PaddleOCR-VL-1.6 if available.

    Returns {"ok": bool, "markdown": str | None, "json": dict | None, "engine": str}.
    """
    try:
        # PaddleX / PaddleOCR-VL would be imported here
        # from paddleocr import PaddleOCRVL  # type: ignore
        # For now return unavailable
        return {"ok": False, "markdown": None, "json": None, "engine": "paddle-unavailable",
                "reason": "PaddleOCR-VL not installed — see requirements-ocr-advanced.txt"}
    except Exception as e:
        return {"ok": False, "markdown": None, "json": None, "engine": "error", "reason": str(e)}
