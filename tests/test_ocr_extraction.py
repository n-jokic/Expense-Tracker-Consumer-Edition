"""
OCR-01 regression tests — line-item extraction and total reconciliation.

Parser unit tests run on synthetic OCR text (no image needed); the
fixture-based tests read the committed REAL receipt images with Tesseract
and skip gracefully when the engine is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.receipt.line_item_extractor import (
    EUR_TOLERANCE,
    extract_line_items,
    reconcile,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "receipts"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _has_tesseract() -> bool:
    try:
        from ocr import _find_tesseract
        return bool(_find_tesseract())
    except Exception:
        return False


# ── Parser unit tests on synthetic OCR text ──────────────────────────────────

RSD_TEXT = """LIDL
Beograd 11000
17.08.2026 14:32
----------------------------------
Mleko 2 x 289,99 579,98
Hleb 79,99
Piletina file 1.249,99
Jaja 149,99
Kafa 280,55
----------------------------------
Medjuzbir 2.340,50
UKUPNO 2.340,50
Gotovina 5.000,00
Kusur 2.659,50
"""


def test_rsd_lines_extracted_with_thousands_and_qty():
    items = extract_line_items(RSD_TEXT)
    names = [i["description"] for i in items]
    assert "Hleb" in names and "Kafa" in names
    milk = next(i for i in items if i["description"] == "Mleko")
    assert milk["quantity"] == 2
    assert milk["unit_price"] == pytest.approx(289.99)
    assert milk["line_total"] == pytest.approx(579.98)
    chicken = next(i for i in items if i["description"] == "Piletina file")
    assert chicken["line_total"] == pytest.approx(1249.99)
    # meta lines never become products
    assert all("UKUPNO" not in n.upper() for n in names)
    assert all("Kusur" not in n for n in names)
    assert reconcile(items, 2340.50)["ok"] is True


EUR_TEXT = """TESCO
12/08/2026
----------------------
Milk 2 x 0.89 1.78
Bread 1.15
Wine 6.99
Discount -0.99
----------------------
TOTAL 8.93
"""


def test_eur_decimal_point_and_negative_discount_line():
    items = extract_line_items(EUR_TEXT)
    discount = [i for i in items if i["line_total"] < 0]
    assert len(discount) == 1
    assert discount[0]["line_total"] == pytest.approx(-0.99)
    assert discount[0]["description"].lower().startswith("discount")
    rec = reconcile(items, 8.93)
    assert rec["items_total"] == pytest.approx(8.93)
    assert rec["ok"] is True


def test_reconciliation_reports_mismatch_without_clamping():
    items = [{"description": "A", "quantity": 1, "unit_price": 1.0, "line_total": 1.0},
             {"description": "B", "quantity": 1, "unit_price": 2.0, "line_total": 2.0}]
    rec = reconcile(items, 5.00)
    assert rec == {"items_total": 3.0, "stated_total": 5.00,
                   "delta": 2.0, "ok": False}


def test_reconciliation_tolerance_is_one_cent():
    items = [{"description": "A", "quantity": 1, "unit_price": 1.0, "line_total": 1.0}]
    assert reconcile(items, 1.01)["ok"] is True       # exactly EUR_TOLERANCE
    assert reconcile(items, 1.02)["ok"] is False


def test_empty_and_garbage_inputs_yield_empty_items():
    assert extract_line_items(None) == []
    assert extract_line_items("") == []
    assert extract_line_items("17.08.2026\n14:32\nLIDL Beograd") == []


# ── Fixture-based end-to-end (real images + Tesseract) ──────────────────────

@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract not installed")
@pytest.mark.parametrize("case", MANIFEST, ids=lambda c: c["id"])
def test_real_receipt_fixtures_extract_and_reconcile(case):
    from ocr import ocr_image

    image_path = FIXTURE_DIR / case["image"]
    text, reason = ocr_image(image_path.read_bytes())
    if text is None:
        pytest.skip(f"OCR engine could not read {case['id']}: {reason}")
    items = extract_line_items(text)
    assert items, f"no line items extracted from {case['id']}"

    rec = reconcile(items, case["total"])
    if case["quality"] == "clean":
        # clean fixtures must recover every item row and reconcile exactly
        assert len(items) == len(case["items"]), (
            f"{case['id']}: {len(items)}/{len(case['items'])} items — {text!r}")
        assert rec["ok"] is bool(case["reconciles"])
        if case["reconciles"]:
            assert abs(rec["delta"]) <= EUR_TOLERANCE
    else:
        # degraded captures: extraction runs and stays well-formed; exact
        # recovery depends on engine quality and is not asserted here.
        assert set(rec) >= {"items_total", "stated_total", "delta", "ok"}


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract not installed")
def test_mismatch_fixture_reports_delta():
    from ocr import ocr_image

    case = next(c for c in MANIFEST if c["id"] == "mismatch-eur-latin")
    text, reason = ocr_image((FIXTURE_DIR / case["image"]).read_bytes())
    if text is None:
        pytest.skip(f"OCR unavailable: {reason}")
    items = extract_line_items(text)
    assert items
    rec = reconcile(items, case["total"])
    # Engine-relative honesty: whatever rows survived OCR, the mismatch
    # against the inflated stated total must be REPORTED, never accepted.
    assert rec["ok"] is False
    assert rec["delta"] == pytest.approx(
        case["total"] - round(sum(i["line_total"] for i in items), 2),
        abs=EUR_TOLERANCE)
    assert rec["delta"] > EUR_TOLERANCE   # stated total is the inflated one
