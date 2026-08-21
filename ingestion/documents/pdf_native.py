"""ingestion/documents/pdf_native.py — native PDF text extraction (pdfplumber)."""

from __future__ import annotations

def extract_native_pdf_text(pdf_bytes: bytes) -> tuple[str, bool]:
    """Extract text via pdfplumber if available.

    Returns (text, has_enough_text). Second value indicates if native path is sufficient.
    """
    try:
        import pdfplumber  # type: ignore
        import io
        text_parts: list[str] = []
        table_rows = 0
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    t = page.extract_text() or ""
                    if t:
                        text_parts.append(t)
                    tables = page.extract_tables() or []
                    table_rows += sum(len(tbl) for tbl in tables)
                except Exception:
                    continue
        text = "\n".join(text_parts)
        has_enough = len(text.splitlines()) >= 3 or table_rows >= 3
        return text, has_enough
    except Exception:
        return "", False
