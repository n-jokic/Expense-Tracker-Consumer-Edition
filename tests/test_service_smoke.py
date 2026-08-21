"""
Smoke tests for the canonical finance query services
(services/finance_queries.py) and the MCP adapters that delegate to them.

Verifies that the read/query arithmetic lives in one place and that
mcp_server._expense_summary_impl returns the same shape as before while
delegating to the canonical module (no duplicated arithmetic).

Follows the existing test pattern: init_db() + create_user, seed with
add_expense/add_income/add_savings, then assert shapes. Uses the conftest DB
isolation (throwaway SQLCipher DB under data/_pytest_tmp).
"""

import asyncio
from datetime import date, timedelta

import pandas as pd
import pytest

import db
import services.finance_queries as fq
import mcp_server as mcp
from db import (init_db, create_user, delete_user_account, username_exists,
                get_user_by_username, add_expense, add_income, add_savings,
                add_budget, get_savings, save_settings)
from auth import hash_password

TEST_USERNAME = "fq_smoke_user"
TEST_EMAIL = "fq_smoke@example.com"


@pytest.fixture()
def seeded_user():
    """A user with a fixed month (2025-06) of expenses/income + one savings goal."""
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "FQ Smoke")
    add_expense(uid, {"date": date(2025, 6, 5), "category": "Groceries",
                      "subcategory": "Groceries", "description": "Lidl shop",
                      "amount": 10.0, "currency": "EUR", "amount_eur": 10.0})
    add_expense(uid, {"date": date(2025, 6, 20), "category": "Transport",
                      "description": "bus ticket", "amount": 2.5,
                      "currency": "EUR", "amount_eur": 2.5})
    add_income(uid, {"date": date(2025, 6, 1), "source": "Salary",
                     "income_type": "Salary", "budgeted": 100.0, "actual": 100.0,
                     "currency": "EUR", "budgeted_eur": 100.0,
                     "actual_eur": 100.0, "notes": ""})
    add_savings(uid, {"date": date(2025, 6, 1), "goal_name": "Vacation",
                      "target_eur": 1000.0, "deposited": 200.0,
                      "currency": "EUR", "deposited_eur": 200.0,
                      "interest_rate": 0.0, "balance_eur": 200.0, "notes": ""})
    yield uid
    delete_user_account(uid)


def _df(rows):
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ── Canonical read services (DB) ─────────────────────────────────────────────

def test_get_expense_summary_shape(seeded_user):
    s = fq.get_expense_summary(seeded_user, "2025-06")
    assert s["month"] == "2025-06"
    assert s["spent_eur"] == 12.5
    assert s["income_eur"] == 100.0
    assert s["net_eur"] == 87.5
    assert s["budget_total_eur"] == 0.0
    assert s["budget_remaining_eur"] == -12.5
    assert s["top_category"] == {"category": "Groceries", "amount_eur": 10.0}
    assert s["month_start"] == date(2025, 6, 1)
    assert s["month_end"] == date(2025, 7, 1)


def test_list_expenses_shape_and_filter(seeded_user):
    res = fq.list_expenses(seeded_user, "2025-06")
    assert res["count"] == 2
    assert res["total_eur"] == 12.5
    assert isinstance(res["expenses"], pd.DataFrame)
    assert set(res["expenses"]["category"]) == {"Groceries", "Transport"}

    only = fq.list_expenses(seeded_user, "2025-06", category="transport")
    assert only["count"] == 1
    assert only["expenses"].iloc[0]["description"] == "bus ticket"

    none = fq.list_expenses(seeded_user, "2025-05")
    assert none["count"] == 0 and none["total_eur"] == 0.0


def test_search_expenses_shape(seeded_user):
    res = fq.search_expenses(seeded_user, "lidl")
    assert res["count"] == 1
    assert res["expenses"].iloc[0]["amount_eur"] == 10.0

    none = fq.search_expenses(seeded_user, "zzz-no-match")
    assert none["count"] == 0

    with pytest.raises(ValueError):
        fq.search_expenses(seeded_user, "   ")


def test_list_income_shape(seeded_user):
    res = fq.list_income(seeded_user, "2025-06")
    assert res["count"] == 1
    assert res["total_eur"] == 100.0
    assert isinstance(res["income"], pd.DataFrame)
    assert res["income"].iloc[0]["income_type"] == "Salary"


def test_get_savings_summary_shape(seeded_user):
    s = fq.get_savings_summary(seeded_user)
    assert s["total_balance_eur"] == 200.0
    assert s["interest_total_eur"] == 0.0
    assert isinstance(s["goals"], list) and len(s["goals"]) == 1
    goal = s["goals"][0]
    assert goal["goal_name"] == "Vacation"
    assert goal["balance_eur"] == 200.0
    assert goal["target_eur"] == 1000.0


def test_get_savings_summary_empty(seeded_user):
    # A fresh user with no savings → empty summary, no exceptions.
    init_db()
    uid = create_user("fq_empty_savings", "fq_empty_savings@example.com",
                      hash_password("test1234"), "Empty Savings")
    try:
        s = fq.get_savings_summary(uid)
        assert s == {"goals": [], "total_balance_eur": 0.0, "interest_total_eur": 0.0}
    finally:
        delete_user_account(uid)


# ── Pure analysis helpers ─────────────────────────────────────────────────────

def test_month_over_month_canonical():
    df = _df([
        {"date": "2025-05-05", "amount_eur": 100.0},
        {"date": "2025-06-05", "amount_eur": 150.0},
    ])
    m = fq.month_over_month(df, "amount_eur", 2025, 6)
    assert m == {"current": 150.0, "previous": 100.0, "change_pct": 50.0, "trend": "up"}


def test_top_category_this_month_canonical():
    df = _df([
        {"date": "2025-06-01", "category": "Food", "amount_eur": 5.0},
        {"date": "2025-06-02", "category": "Food", "amount_eur": 5.0},
        {"date": "2025-06-03", "category": "Transport", "amount_eur": 30.0},
    ])
    assert fq.top_category_this_month(df, 2025, 6) == ("Transport", 30.0)
    assert fq.top_category_this_month(pd.DataFrame(), 2025, 6) is None


def test_unusual_expenses_canonical():
    df = _df([
        {"date": "2025-06-01", "category": "Food", "amount_eur": 10.0},
        {"date": "2025-06-02", "category": "Food", "amount_eur": 12.0},
        {"date": "2025-06-03", "category": "Food", "amount_eur": 200.0},
    ])
    out = fq.unusual_expenses(df, multiplier=2.0)
    assert len(out) == 1
    assert out.iloc[0]["amount_eur"] == 200.0


def test_days_until_budget_depleted_canonical():
    today = date.today()
    df = _df([{"date": today.isoformat(), "amount_eur": 20.0}])
    days = fq.days_until_budget_depleted(df, 100.0, today.replace(day=1))
    assert days is not None and days > 0

    # Over budget → 0.
    df_over = _df([{"date": today.isoformat(), "amount_eur": 500.0}])
    assert fq.days_until_budget_depleted(df_over, 100.0, today.replace(day=1)) == 0

    # No budget / no expenses → None.
    assert fq.days_until_budget_depleted(df, 0.0, today.replace(day=1)) is None
    assert fq.days_until_budget_depleted(pd.DataFrame(), 100.0, today.replace(day=1)) is None


def test_savings_projection_canonical():
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 100.0,
         "target_eur": 300.0, "deposited_eur": 100.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "balance_eur": 200.0,
         "target_eur": 300.0, "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    p = fq.savings_projection(df, "G")
    assert p["months_to_goal"] == 1
    assert p["projected_date"] is not None

    assert fq.savings_projection(pd.DataFrame(), "G")["months_to_goal"] is None


def test_build_narrative_stats_canonical():
    df = _df([
        {"date": "2025-05-01", "category": "Food", "description": "a",
         "amount_eur": 50.0},
        {"date": "2025-06-01", "category": "Food", "description": "b",
         "amount_eur": 20.0},
        {"date": "2025-06-02", "category": "Food", "description": "c",
         "amount_eur": 30.0},
    ])
    stats = fq.build_narrative_stats(df, {"monthly_budget": 200.0}, 2025, 6)
    assert stats["spent_eur"] == 50.0
    assert stats["prev_spent_eur"] == 50.0
    assert stats["change_pct"] == 0.0
    assert stats["top_category"] == "Food (50.00 EUR)"
    assert stats["budget_remaining"] == 150.0


# ── MCP adapter delegation ────────────────────────────────────────────────────

def test_mcp_imports_canonical_module():
    # Import check: mcp_server must import services.finance_queries (no dup math).
    assert hasattr(mcp, "fq")
    assert mcp.fq is fq


def test_mcp_expense_summary_delegates_same_shape(seeded_user, monkeypatch):
    monkeypatch.setattr(mcp, "_USER_ID", None)
    monkeypatch.setattr(mcp, "MCP_USERNAME", TEST_USERNAME)
    res = asyncio.run(mcp._expense_summary_impl("2025-06"))

    # Same top-level shape the adapter exposed before the refactor.
    assert res["ok"] is True
    assert set(res) == {
        "ok", "month", "spent_eur", "income_eur", "net_eur",
        "budget_total_eur", "budget_remaining_eur", "top_category",
        "fun_money_eur", "monthly_budget_eur",
    }
    assert res["spent_eur"] == 12.5
    assert res["income_eur"] == 100.0
    assert res["net_eur"] == 87.5
    assert res["top_category"] == {"category": "Groceries", "amount_eur": 10.0}

    # It mirrors the canonical service for the same seeded user (delegation,
    # not a parallel implementation).
    canonical = fq.get_expense_summary(seeded_user, "2025-06")
    assert res["month"] == canonical["month"]
    assert res["spent_eur"] == canonical["spent_eur"]
    assert res["income_eur"] == canonical["income_eur"]
    assert res["net_eur"] == canonical["net_eur"]
    assert res["budget_total_eur"] == canonical["budget_total_eur"]
    assert res["budget_remaining_eur"] == canonical["budget_remaining_eur"]
    assert res["top_category"] == canonical["top_category"]
