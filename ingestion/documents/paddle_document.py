"""Optional heavy document parsers (PP-StructureV3 / PaddleOCR-VL-1.6)."""

from __future__ import annotations

import os
import tempfile

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

def _unavailable(reason: str) -> dict:
    return {
        "ok": False,
        "markdown": None,
        "json": None,
        "engine": "paddle-unavailable",
        "reason": reason,
    }


def _normalize_result(raw) -> dict:
    if isinstance(raw, dict):
        data = raw
        markdown = raw.get("markdown") or raw.get("markdown_text")
    elif hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        pages = [_normalize_result(item) for item in raw]
        markdowns = [page["markdown"] for page in pages if page["markdown"]]
        return {
            "ok": any(page["ok"] for page in pages),
            "markdown": "\n\n".join(markdowns) or None,
            "json": {"pages": [page["json"] for page in pages if page["json"]]},
            "engine": "PaddleOCR-VL",
        }
    else:
        data = getattr(raw, "json", None)
        markdown = getattr(raw, "markdown", None)
        if callable(data):
            data = data()
        if callable(markdown):
            markdown = markdown()
    if isinstance(markdown, dict):
        markdown_data = markdown
        markdown = markdown_data.get("text") or markdown_data.get("markdown_text")
        if not markdown:
            values = markdown_data.get("markdown_texts", {})
            values = values.values() if isinstance(values, dict) else values
            markdown = "\n".join(str(value) for value in values)
    return {
        "ok": bool(markdown or data),
        "markdown": str(markdown) if markdown else None,
        "json": data if isinstance(data, dict) else None,
        "engine": "PaddleOCR-VL",
    }


def parse_complex_document(image_bytes: bytes) -> dict:
    """Parse complex layout when the optional PaddleOCR stack is installed."""
    if not image_bytes or len(image_bytes) > MAX_DOCUMENT_BYTES:
        return _unavailable("document is empty or exceeds the 20 MB limit")
    try:
        from paddleocr import PaddleOCRVL  # type: ignore
    except ImportError:
        return _unavailable("PaddleOCR-VL not installed — see requirements-ocr-advanced.txt")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_file.write(image_bytes)
            temp_path = temp_file.name
        parser = PaddleOCRVL()
        return _normalize_result(parser.predict(input=temp_path))
    except Exception as exc:
        return {
            "ok": False,
            "markdown": None,
            "json": None,
            "engine": "paddle-error",
            "reason": str(exc),
        }
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
