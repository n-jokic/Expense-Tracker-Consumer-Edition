"""
Tests for receipt OCR extraction (ocr.py) — Tesseract itself is mocked.
"""

import pandas as pd
import pytest

import ocr
from ocr import extract_amounts, guess_total_amount, guess_merchant, analyze_receipt


def test_extract_amounts_european_format():
    assert extract_amounts("Kafa 1.234,56") == [1234.56]
    assert extract_amounts("Ukupno: 3.500,00 din") == [3500.0]


def test_extract_amounts_us_format():
    assert extract_amounts("Total 1,234.56") == [1234.56]


def test_extract_amounts_plain_decimals():
    assert 12.5 in extract_amounts("item 12.50 item 4.99")


def test_guess_total_prefers_total_line():
    text = "Market ABC\nBread 120,00\nMilk 180,00\nUKUPNO 300,00"
    assert guess_total_amount(text) == 300.0


def test_guess_total_falls_back_to_largest():
    text = "Market ABC\nBread 120,00\nMilk 180,00"
    assert guess_total_amount(text) == 180.0


def test_guess_merchant_skips_noise():
    text = ("01/02/2025 14:33\n"
            "MAXI SUPERMARKET\n"
            "Bread 120,00\n"
            "TOTAL 120,00")
    assert guess_merchant(text) == "MAXI SUPERMARKET"


def test_analyze_receipt_without_tesseract(monkeypatch):
    def fake_ocr(_):
        return None

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    res = analyze_receipt(b"not-an-image")
    assert res["ok"] is False
    assert res["reason"] == "ocr_unavailable"


def test_analyze_receipt_keyword_fallback(monkeypatch):
    def fake_ocr(_):
        return "LIDL 1234\nBread 120,00\nTOTAL 120,00"

    monkeypatch.setattr(ocr, "ocr_image", fake_ocr)
    res = analyze_receipt(b"img", expenses_df=None)
    assert res["ok"] is True
    assert res["amount"] == 120.0
    assert res["category"] == "Food & Dining"
    assert res["subcategory"] == "Groceries"
