"""Focused Streamlit coverage for confidence-aware receipt review."""

from __future__ import annotations

import os

import ocr
from ingestion.receipt.models import FieldCandidate, OCRDocument, ReceiptResult
from streamlit.testing.v1 import AppTest

from auth import hash_password
from db import create_user, delete_user_account, init_db, username_exists, get_user_by_username


APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
LOG_EXPENSE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "app_pages", "log_expense.py")
)
USERNAME = "ocr_review_test_user"


def test_receipt_review_shows_uncertainty_and_reuses_result(monkeypatch):
    init_db()
    if username_exists(USERNAME):
        delete_user_account(get_user_by_username(USERNAME)["id"])
    user_id = create_user(USERNAME, "ocr-review@example.com", hash_password("test1234"), "OCR Tester")
    calls = []

    result = ReceiptResult(
        merchant=FieldCandidate("Lidl", 0.94),
        total=FieldCandidate(2340.50, 0.54),
        date=FieldCandidate(__import__("datetime").date(2026, 8, 17), 0.93),
        currency=FieldCandidate("RSD", 0.96),
        alternatives={"total": [FieldCandidate(5000.0, 0.51)]},
        document=OCRDocument(engine="test"),
    )

    def fake_analyze(*_args, **_kwargs):
        calls.append(1)
        return {
            "ok": True,
            "text": "LIDL\nUKUPNO 2.340,50 RSD\nGOTOVINA 5.000,00",
            "amount": 2340.50,
            "merchant": "Lidl",
            "category": "Groceries",
            "subcategory": "Groceries",
            "confidence": 0.9,
            "reason": None,
            "source": "keywords",
            "model_version": None,
            "subcategory_confidence": None,
            "subcategory_source": "keywords",
            "receipt_result": result,
            "document": result.document,
        }

    monkeypatch.setattr(ocr, "analyze_receipt", fake_analyze)
    try:
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        at.session_state["authenticated"] = True
        at.session_state["user_id"] = user_id
        at.session_state["username"] = USERNAME
        at.session_state["display_name"] = "OCR Tester"
        at.session_state["household_id"] = None
        at.session_state["onboarding_complete"] = True
        at.session_state["onboarding_step"] = 0
        at.switch_page(LOG_EXPENSE_PATH)
        at.run()
        assert not at.exception, at.exception

        at.file_uploader[0].set_value(("receipt.jpg", b"not-an-image", "image/jpeg"))
        at.run()
        assert not at.exception, at.exception
        warnings = " ".join(str(getattr(item, "value", "") or "") for item in at.warning)
        assert "multiple possible total" in warnings.lower()
        assert any("Currency" in str(getattr(item, "label", "")) for item in at.selectbox)
        assert len(calls) == 1

        at.run()
        assert not at.exception, at.exception
        assert len(calls) == 1
    finally:
        delete_user_account(user_id)
