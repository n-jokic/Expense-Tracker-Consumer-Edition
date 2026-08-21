"""ingestion/documents/pdf_scanned.py — scanned PDF via PyMuPDF + RapidOCR / reconstruct rows."""

from __future__ import annotations

def render_pdf_pages(pdf_bytes: bytes, dpi: int = 250) -> list[bytes]:
    """Render PDF pages to image bytes via PyMuPDF if available. Returns list of PNG bytes."""
    try:
        import fitz  # PyMuPDF
        import io
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        out: list[bytes] = []
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            out.append(pix.tobytes("png"))
        return out
    except Exception:
        return []

def ocr_scanned_pdf(pdf_bytes: bytes) -> tuple[str, list]:
    """OCR scanned PDF pages via RapidOCR if available. Returns (combined_text, token_boxes)."""
    pages = render_pdf_pages(pdf_bytes)
    if not pages:
        return "", []
    try:
        from ingestion.receipt.service import analyze_receipt
        texts: list[str] = []
        for png in pages:
            res = analyze_receipt(png)
            if res.get("text"):
                texts.append(res["text"])
        return "\n\n".join(texts), []
    except Exception:
        return "", []
