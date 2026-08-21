"""Tests for scanned-PDF OCR fallback and optional document parsing."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from ingestion.documents import pdf_scanned
from ingestion.documents.paddle_document import parse_complex_document
from ingestion.receipt.models import OCRDocument, OCRToken
from ingestion.receipt.service import _tokens_to_text
from ingestion.receipt.total_extractor import extract_total_candidates


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


def test_tokens_to_text_groups_two_tokens_same_line_with_space():
    """FIX 1: same-line tokens must be space-joined, not flat newline-joined."""
    doc = OCRDocument(tokens=[
        OCRToken(text="MAXI", confidence=0.9, polygon=((0, 0), (0, 0), (0, 0), (0, 0)), line_id=0),
        OCRToken(text="SUPERMARKET", confidence=0.9, polygon=((0, 0), (0, 0), (0, 0), (0, 0)), line_id=0),
        OCRToken(text="UKUPNO", confidence=0.9, polygon=((0, 0), (0, 0), (0, 0), (0, 0)), line_id=1),
        OCRToken(text="2.340,50", confidence=0.9, polygon=((0, 0), (0, 0), (0, 0), (0, 0)), line_id=2),
    ], engine="RapidOCR")
    text = _tokens_to_text(doc)
    assert text == "MAXI SUPERMARKET\nUKUPNO\n2.340,50"


def test_tokens_to_text_empty_document():
    assert _tokens_to_text(OCRDocument()) == ""


def test_to_compat_dict_merchant_fallback_sets_zero_conf_cand(monkeypatch):
    """FIX 2: when the merchant extractor misses but guess_merchant hits,
    compat['merchant'] is pre-filled AND receipt_result.merchant is a zero-conf
    FieldCandidate so the uncertainty warning fires instead of contradicting it."""
    from ingestion.receipt.models import ReceiptResult
    from ingestion.receipt.service import _to_compat_dict

    # ReceiptResult with no merchant candidate (simulating an extractor miss).
    result = ReceiptResult(merchant=None, document=OCRDocument(engine="test"))

    monkeypatch.setattr("ocr.guess_merchant", lambda _raw: "Lidl", raising=False)

    compat = _to_compat_dict(result.document, "LIDL\nUKUPNO 120,00", result, None, None, None)

    assert compat["merchant"] == "Lidl"
    rr = compat["receipt_result"]
    assert rr.merchant is not None
    assert rr.merchant.value == "Lidl"
    assert rr.merchant.confidence == 0.0
    assert "fallback:guess_merchant" in (rr.merchant.reasons or ())
    # Original receipt_result instance was NOT mutated (frozen-safe replace).
    assert result.merchant is None


@pytest.mark.parametrize(
    "raw_text, expected",
    [
        # Repro 1 (date phantom): a date on the same line as the total must not
        # leak a phantom amount (e.g. 31.12) that outranks the real total.
        ("31.12.2024 Total 120,00", 120.0),
        ("15.05.2024\nBread 120,00\nTOTAL 120,00", 120.0),
        ("01.05.2024\nTOTAL 120,00", 120.0),
        # Repro 2 (cash/change penalty +1 boost abuse): the real total (190)
        # must beat a cash amount (200) even when 200 is the largest amount.
        ("Total 190,00 Cash 200,00 Change 10,00", 190.0),
        ("Total 190,00 RSD Cash 200,00 Change 10,00", 190.0),
        # Repro 3 (adjacent-line boost abuse): an unpenalized item (250) on the
        # line above TOTAL must not outrank the real total (190) on its line.
        ("Bread 250,00\nTOTAL 190,00 TAX 15,00", 190.0),
        # Sanity: plain total line, currency, total below items, keyword-less.
        ("TOTAL 120,00", 120.0),
        ("UKUPNO 120,00 RSD", 120.0),
        ("MARKET ABC\nBread 120,00\nMilk 80,00\nTOTAL 200,00", 200.0),
        ("Bread 120,00\nMilk 80,00", 120.0),
        # Serbian thousands separator (dot groups) must not truncate to decimals
        # (regression for _AMT_RE decimal-group defect).
        ("UKUPNO 1.234", 1234.0),
        ("UKUPNO 5.000", 5000.0),
        ("Total 1.200", 1200.0),
    ],
)
def test_extract_total_candidates_true_total_wins(raw_text, expected):
    """The real total must be the top-scoring candidate across all scoring fixes."""
    candidates = extract_total_candidates(OCRDocument(), raw_text=raw_text)
    assert candidates and candidates[0].value == expected, (
        f"raw_text={raw_text!r}: expected {expected}, got "
        f"{candidates[0].value if candidates else None} "
        f"(reasons={candidates[0].reasons if candidates else None})"
    )


@pytest.mark.parametrize(
    "raw_text",
    [
        # Dates must be stripped so phantom amounts never enter the pool.
        "15.05.2024 Total 120,00",
        "01.05.2024\nBread 120,00\nTotal 120,00",
    ],
)
def test_extract_total_candidates_no_date_phantoms(raw_text):
    """No phantom amount (e.g. 31.12 / 1.05 / 2024) may appear as a candidate."""
    candidates = extract_total_candidates(OCRDocument(), raw_text=raw_text)
    phantom_values = {1.05, 31.12, 15.05, 2024.0, 15.0, 1.2, 202.0}
    for c in candidates:
        assert c.value not in phantom_values, (
            f"raw_text={raw_text!r}: phantom candidate {c.value} leaked into pool"
        )


def test_extract_total_candidates_thousands_not_truncated():
    """Serbian dot-thousands round totals parse as integers, not decimals."""
    candidates = extract_total_candidates(OCRDocument(), raw_text="UKUPNO 1.234")
    assert candidates and candidates[0].value == 1234.0
    candidates = extract_total_candidates(OCRDocument(), raw_text="UKUPNO 5.000")
    assert candidates and candidates[0].value == 5000.0
