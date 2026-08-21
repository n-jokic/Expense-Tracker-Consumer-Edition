"""Tests for scanned-PDF OCR fallback and optional document parsing."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from ingestion.documents import pdf_scanned
from ingestion.documents.paddle_document import parse_complex_document
from ingestion.receipt.models import OCRDocument, OCRToken


def test_ocr_scanned_pdf_combines_page_text_and_structured_tokens(monkeypatch):
    pages = [b"page-one", b"page-two"]
    monkeypatch.setattr(pdf_scanned, "render_pdf_pages", lambda _pdf, dpi=250: pages)

    def fake_analyze(page, **_kwargs):
        page_no = pages.index(page)
        token = OCRToken(
            text=f"TOTAL {page_no + 1}",
            confidence=0.91,
            polygon=((1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0)),
            line_id=0,
        )
        return {
            "ok": True,
            "text": token.text,
            "document": OCRDocument(tokens=[token], engine="RapidOCR"),
        }

    monkeypatch.setattr("ingestion.receipt.service.analyze_receipt", fake_analyze)

    text, token_boxes = pdf_scanned.ocr_scanned_pdf(b"pdf")

    assert text == "TOTAL 1\n\nTOTAL 2"
    assert token_boxes == [
        {"page": 0, "text": "TOTAL 1", "confidence": 0.91,
         "polygon": ((1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0)), "line_id": 0},
        {"page": 1, "text": "TOTAL 2", "confidence": 0.91,
         "polygon": ((1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0)), "line_id": 0},
    ]


def test_ocr_scanned_pdf_returns_empty_when_pages_cannot_render(monkeypatch):
    monkeypatch.setattr(pdf_scanned, "render_pdf_pages", lambda _pdf, dpi=250: [])

    assert pdf_scanned.ocr_scanned_pdf(b"pdf") == ("", [])


def test_pdf_renderer_rejects_unsafe_size_and_dpi():
    assert pdf_scanned.render_pdf_pages(b"pdf", dpi=71) == []
    assert pdf_scanned.render_pdf_pages(b"pdf", dpi=601) == []
    assert pdf_scanned.render_pdf_pages(b"x" * (pdf_scanned.MAX_PDF_BYTES + 1)) == []


def test_complex_parser_is_optional_and_has_stable_result_shape(monkeypatch):
    real_import = __import__("builtins").__import__

    def no_paddle(name, *args, **kwargs):
        if name.startswith("paddleocr") or name.startswith("paddlex"):
            raise ImportError("optional parser absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_paddle)

    result = parse_complex_document(b"image")

    assert result["ok"] is False
    assert result["engine"] == "paddle-unavailable"
    assert result["markdown"] is None
    assert result["json"] is None


def test_complex_parser_normalizes_paddle_generator(monkeypatch):
    class FakeResult:
        json = {"table": [["Total", "12.00"]]}
        markdown = {"markdown_texts": {"0": "| Total | 12.00 |"}}

    class FakePipeline:
        def predict(self, *, input):
            assert input.endswith(".png")
            yield FakeResult()

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCRVL=FakePipeline))

    result = parse_complex_document(b"image")

    assert result["ok"] is True
    assert result["engine"] == "PaddleOCR-VL"
    assert result["markdown"] == "| Total | 12.00 |"
    assert result["json"]["pages"][0] == {"table": [["Total", "12.00"]]}
