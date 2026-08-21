"""
OCR-02 regression tests — row-level receipt review with an atomic bulk save.

One command = one transaction = one audit group for the whole receipt;
validation precedes any write; mid-transaction failures roll back every
row; the source receipt total is retained. The review page no longer nests
a Streamlit form inside the scan expander (the recorded nested-forms bug).
"""

from datetime import date

import pytest

import db
import services.commands as cmd
from auth import hash_password
from services.commands import ReceiptItemsError, save_receipt_items

U = "ocr02_user"
E = "ocr02@example.com"

ITEMS = [
    {"description": "Croissant", "quantity": 1, "unit_price": 1.2,
     "line_total": 1.2, "amount_eur": 1.2},
    {"description": "Baguette", "quantity": 1, "unit_price": 1.85,
     "line_total": 1.85, "amount_eur": 1.85},
    {"description": "Coffee", "quantity": 2, "unit_price": 1.1,
     "line_total": 2.2, "amount_eur": 2.2},
]


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "OCR02 Tester")
    yield uid
    db.delete_user_account(uid)


def _expenses(uid):
    return db.get_expenses(uid)


# ── Happy path ───────────────────────────────────────────────────────────────

def test_bulk_save_writes_all_rows_one_audit_group(user):
    res = save_receipt_items(user, ITEMS, entry_date=date(2026, 8, 21),
                             currency="EUR", category="Food & Dining",
                             subcategory="Groceries", notes="Pekara",
                             source_total_eur=15.0, merchant="Pekara")
    assert res.changed and len(res.affected_ids) == 3
    df = _expenses(user)
    assert len(df) == 3
    assert set(df["description"]) == {"Croissant", "Baguette", "Coffee"}
    assert df["amount_eur"].sum() == pytest.approx(5.25)
    # the source receipt total is retained on the rows
    assert df["notes"].str.contains(r"\[receipt total 15\.00\]").all()
    # exactly ONE audit group for the whole receipt
    audits = db.get_audit_log(user)
    bulk = audits[audits["action"] == "BULK_CREATE"]
    assert len(bulk) == 1
    import json as _json
    details = _json.loads(bulk.iloc[0]["details"] or "{}")
    assert details["items"] == 3
    assert details["source_total_eur"] == 15.0


# ── All-or-nothing guarantees ────────────────────────────────────────────────

def test_invalid_item_aborts_before_any_write(user):
    bad = ITEMS + [{"description": "", "amount_eur": 9.99}]
    with pytest.raises(ReceiptItemsError):
        save_receipt_items(user, bad, source_total_eur=15.0)
    assert len(_expenses(user)) == 0          # nothing was written


def test_zero_amount_item_rejected_atomically(user):
    bad = [{"description": "Fine", "amount_eur": 2.0},
           {"description": "Bad", "amount_eur": 0.0}]
    with pytest.raises(ReceiptItemsError):
        save_receipt_items(user, bad)
    assert len(_expenses(user)) == 0


def test_mid_transaction_failure_rolls_back_everything(user, monkeypatch):
    """A failure after rows were added (before commit) leaves zero rows."""
    import services.commands as mod
    real_log_audit = db.log_audit

    def exploding_audit(*args, **kwargs):
        raise RuntimeError("injected disk failure")

    monkeypatch.setattr(db, "log_audit", exploding_audit)
    try:
        with pytest.raises(RuntimeError):
            save_receipt_items(user, ITEMS, source_total_eur=15.0)
    finally:
        monkeypatch.setattr(db, "log_audit", real_log_audit)
    assert len(_expenses(user)) == 0
    # a clean retry afterwards works — no partial state leaked
    res = save_receipt_items(user, ITEMS[:1], source_total_eur=1.2)
    assert res.changed and len(_expenses(user)) == 1


def test_empty_batch_is_a_clean_noop(user):
    with pytest.raises(ReceiptItemsError):
        save_receipt_items(user, [])
    assert len(_expenses(user)) == 0


# ── Page contract: de-nested review, opt-in gate ────────────────────────────

def _page(name):
    from pathlib import Path
    return Path(__file__).resolve().parents[1].joinpath(
        "app_pages", name).read_text(encoding="utf-8")


def test_receipt_review_page_has_no_nested_form():
    src = _page("log_expense.py")
    # the old nested st.form("receipt_form") is gone entirely…
    assert 'st.form("receipt_form")' not in src
    # …row-level widgets exist with stable per-item keys…
    assert 'key=f"rcpt_item_keep_{idx}"' in src
    assert 'key=f"rcpt_item_desc_{idx}"' in src
    # …and the mismatch gate requires an explicit confirmation.
    assert "Import anyway" in src
    assert 'key="rcpt_mismatch_confirm"' in src


def test_images_never_uploaded_without_explicit_opt_in():
    page = _page("log_expense.py")
    assert "Images never leave this PC" in page
    assert 'get_settings(user_id).get("ocr_cloud_fallback")' in page
    settings_src = _page("settings_ai.py")
    assert '"Allow cloud OCR fallback for unreadable receipts "' in settings_src
    assert 'value=bool(settings.get("ocr_cloud_fallback"))' in settings_src


# NB: the full-app AppTest coverage for the scan flow lives in
# tests/test_ocr_review.py::test_receipt_review_shows_uncertainty_and_reuses_result,
# which now passes because the nested receipt form is gone. A second AppTest
# here would only add sandbox-tmp-cleanup flakiness (see recorded env noise).
