"""research.md L3 — advisor budget proposals apply via ONE audited command.

Covers the server-side command (validation + upsert + revision bump) and the
proposal shape safety.py emits (period pinned at proposal time). All hermetic.
"""

from datetime import date

import pytest

from ai.safety import check_mutation_proposal
from services.commands import CommandError, set_budget
from db import get_budgets, init_db, create_user, delete_user_account, \
    username_exists, get_user_by_username
from auth import hash_password

TEST_USERNAME = "confirm_test_user"
TEST_EMAIL = "confirm_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Confirm Tester")
    yield uid
    delete_user_account(uid)


def test_set_budget_creates_row_and_revision(test_user):
    res = set_budget(test_user, "Groceries", 250.0)
    assert res.changed is True
    assert isinstance(res.revision, int)
    df = get_budgets(test_user)
    row = df[(df["year"] == date.today().year)
             & (df["month"] == date.today().month)
             & (df["category"] == "Groceries")]
    assert len(row) == 1 and float(row.iloc[0]["budgeted_eur"]) == 250.0


def test_set_budget_upserts_same_scope_once(test_user):
    set_budget(test_user, "Transport", 100.0)
    set_budget(test_user, "Transport", 150.0)
    df = get_budgets(test_user)
    rows = df[(df["category"] == "Transport")
              & (df["year"] == date.today().year)]
    assert len(rows) == 1 and float(rows.iloc[0]["budgeted_eur"]) == 150.0


def test_set_budget_explicit_period(test_user):
    set_budget(test_user, "Dining Out", 80.0, year=2025, month=3)
    df = get_budgets(test_user)
    row = df[(df["year"] == 2025) & (df["month"] == 3)
             & (df["category"] == "Dining Out")]
    assert len(row) == 1


@pytest.mark.parametrize("kwargs", [
    {"category": "Not A Category", "amount_eur": 50.0},
    {"category": "Groceries", "amount_eur": 0.0},
    {"category": "Groceries", "amount_eur": -5.0},
    {"category": "Groceries", "amount_eur": float("nan")},
    {"category": "Groceries", "amount_eur": 2_000_000.0},
    {"category": "Groceries", "amount_eur": 50.0, "month": 13},
])
def test_set_budget_rejects_invalid_input(test_user, kwargs):
    with pytest.raises(CommandError):
        set_budget(test_user, **kwargs)


def test_proposal_pins_current_period_and_fields():
    p = check_mutation_proposal("Set my Groceries budget to 250")
    assert p is not None
    assert p["type"] == "budget_change"
    assert p["category"] == "Groceries"
    assert p["amount_eur"] == pytest.approx(250.0)
    assert p["requires_confirmation"] is True
    today = date.today()
    assert (p["year"], p["month"]) == (today.year, today.month)


def test_non_mutation_questions_get_no_proposal():
    assert check_mutation_proposal("How much did I spend this month?") is None
