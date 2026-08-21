"""Scanned-PDF fallback via PyMuPDF + the existing receipt OCR service."""

from __future__ import annotations

from ingestion.receipt.models import OCRDocument

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
MIN_RENDER_DPI = 72
MAX_RENDER_DPI = 600


def _token_box(page: int, token) -> dict:
    return {
        "page": page,
        "text": token.text,
        "confidence": float(token.confidence),
        "polygon": token.polygon,
        "line_id": int(token.line_id),
    }


def render_pdf_pages(pdf_bytes: bytes, dpi: int = 250) -> list[bytes]:
    """Render PDF pages to PNG bytes, or return an empty list when unavailable."""
    if not pdf_bytes or len(pdf_bytes) > MAX_PDF_BYTES:
        return []
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        return []
    if not MIN_RENDER_DPI <= dpi <= MAX_RENDER_DPI:
        return []
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        out: list[bytes] = []
        try:
            for page_no in range(min(doc.page_count, MAX_PDF_PAGES)):
                page = doc.load_page(page_no)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                out.append(pix.tobytes("png"))
        finally:
            doc.close()
        return out
    except Exception:
        return []


def ocr_scanned_pdf(pdf_bytes: bytes) -> tuple[str, list]:
    """OCR scanned PDF pages and return combined text plus page token boxes."""
    pages = render_pdf_pages(pdf_bytes)
    if not pages:
        return "", []
    try:
        from ingestion.receipt.service import analyze_receipt

        texts: list[str] = []
        token_boxes: list[dict] = []
        for page_no, png in enumerate(pages):
            try:
                result = analyze_receipt(png)
            except Exception:
                continue
            document = result.get("document")
            if not isinstance(document, OCRDocument):
                receipt_result = result.get("receipt_result")
                document = getattr(receipt_result, "document", None)

            page_text = str(result.get("text") or "").strip()
            if isinstance(document, OCRDocument):
                token_boxes.extend(_token_box(page_no, token) for token in document.tokens)
                if not page_text and document.tokens:
                    by_line: dict[int, list[str]] = {}
                    for token in document.tokens:
                        by_line.setdefault(token.line_id, []).append(token.text)
                    page_text = "\n".join(
                        " ".join(by_line[line]) for line in sorted(by_line)
                    )
            if page_text:
                texts.append(page_text)
        return "\n\n".join(texts), token_boxes
    except Exception:
        return "", []
