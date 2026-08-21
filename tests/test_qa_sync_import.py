"""
QA-01 regression tests — sync create rejects isolated derived-EUR fields,
and CSV parsing agrees with PDF parsing on comma-thousands.
"""

import pandas as pd
import pytest

import db
from auth import hash_password
from sync_core import apply_changes

U = "qa01_user"
E = "qa01@example.com"


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "QA01 Tester")
    yield uid
    db.delete_user_account(uid)


# ── Sync create: isolated derived-EUR rejected ───────────────────────────────

def test_create_rejects_isolated_amount_eur(user):
    res = apply_changes(user, [{
        "table": "expenses",
        "id": "cli-poison",
        "fields": {"date": "2026-08-01", "category": "Other",
                   "description": "poison", "amount_eur": 9999.99},
    }], since=None)
    assert res["failed"], f"expected rejection, got {res}"
    assert "amount_eur" in res["failed"][0]["error"]
    assert not res["applied"]
    assert len(db.get_expenses(user)) == 0


def test_create_with_base_amount_gets_server_computed_eur(user):
    from utils import get_rates, to_eur
    res = apply_changes(user, [{
        "table": "expenses",
        "id": "cli-legit",
        "fields": {"date": "2026-08-01", "category": "Other",
                   "description": "legit", "amount": 100.0,
                   "currency": "RSD"},
    }], since=None)
    assert [a["status"] for a in res["applied"]] == ["created"], res
    df = db.get_expenses(user)
    assert len(df) == 1
    rates = get_rates(db.get_settings(user))
    expected = to_eur(100.0, "RSD", rates)
    assert abs(float(df.iloc[0]["amount_eur"]) - expected) < 0.01


def test_create_with_poisoned_derived_is_recomputed_or_rejected(user):
    """A create that includes base + derived: the server value must win.
    (An out-of-range derived value fails the >0 rule up front; an in-range
    lie like 1.00 for RSD 100 is overwritten by the recompute.)"""
    res = apply_changes(user, [{
        "table": "expenses",
        "id": "cli-lie",
        "fields": {"date": "2026-08-01", "category": "Other",
                   "description": "lie", "amount": 100.0,
                   "currency": "RSD", "amount_eur": 1.00},
    }], since=None)
    assert [a["status"] for a in res["applied"]] == ["created"], res
    rates = db.get_settings(user) or {}
    from utils import get_rates, to_eur
    expected = to_eur(100.0, "RSD", get_rates(rates))
    row = db.get_expenses(user).iloc[0]
    assert abs(float(row["amount_eur"]) - expected) < 0.01
    assert float(row["amount_eur"]) != 1.00 or expected == 1.00


def test_isolated_income_actual_eur_also_rejected(user):
    res = apply_changes(user, [{
        "table": "income",
        "id": "cli-inc",
        "fields": {"date": "2026-08-01", "source": "job",
                   "actual_eur": 5000.0},
    }], since=None)
    assert res["failed"] and not res["applied"]


# ── Direct unit proof of the rejection rule ─────────────────────────────────

def test_recompute_rejects_isolated_derived_on_create():
    from sync_core import _reject_isolated_derived_eur
    with pytest.raises(ValueError):
        _reject_isolated_derived_eur("expenses", {"amount_eur": 10.0})
    with pytest.raises(ValueError):
        _reject_isolated_derived_eur("income", {"actual_eur": 10.0})
    with pytest.raises(ValueError):
        _reject_isolated_derived_eur("savings", {"deposited_eur": 10.0})
    # base present -> fine
    _reject_isolated_derived_eur("expenses", {"amount": 5.0, "currency": "EUR",
                                              "amount_eur": 5.0})


# ── CSV parsing consistent with PDF parsing ─────────────────────────────────

TOKENS = [
    "1,234",          # comma thousands
    "1.234",          # Serbian dot thousands
    "12.345.678",     # multi-group dot thousands
    "1,234.56",       # US mixed
    "1.234,56",       # EU mixed
    "12,50",          # EU decimal
    "1,5",            # short EU decimal
    "0.99",           # plain decimal
    "1200",           # integer
    "-2,75",          # signed EU decimal
]


def test_csv_and_pdf_parsers_agree_on_all_token_forms():
    from bank_import import _to_numeric_locale
    from pdf_import import _parse_amount_core

    csv_vals = _to_numeric_locale(pd.Series(TOKENS, dtype=object)).tolist()
    for tok, csv_v in zip(TOKENS, csv_vals):
        pdf_v = _parse_amount_core(tok)
        assert pdf_v is not None, tok
        assert float(csv_v) == pytest.approx(pdf_v, abs=1e-9), (
            f"CSV/PDF disagree on {tok!r}: {csv_v} vs {pdf_v}")


def test_comma_thousands_never_become_decimals():
    from bank_import import _to_numeric_locale
    out = _to_numeric_locale(pd.Series(["1,234", "12,345"],
                                       dtype=object)).tolist()
    assert out == [1234.0, 12345.0]
