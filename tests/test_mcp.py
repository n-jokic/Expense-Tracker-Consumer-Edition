"""
MCP server tests: user resolution, month/date parsing, read tools, write
tools (validation + audit origin), and insight reuse. Tools are tested via
their *_impl functions directly — no server is started.
"""

import asyncio
import json
from datetime import date, timedelta

import pytest

import mcp_server as mcp
from db import init_db, create_user, delete_user_account, username_exists, \
    get_user_by_username, get_settings, save_settings, get_expenses, \
    get_income, get_data_revision, add_expense as db_add_expense
from auth import hash_password

TEST_USERNAME = "mcp_test_user"
TEST_EMAIL    = "mcp_test@example.com"


@pytest.fixture()
def test_user(monkeypatch):
    init_db()
    monkeypatch.setattr(mcp, "_USER_ID", None)
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "MCP Tester")
    yield uid
    monkeypatch.setattr(mcp, "_USER_ID", None)
    delete_user_account(uid)


def _seed(uid):
    db_add_expense(uid, {"date": date(2025, 6, 5), "category": "Groceries",
                         "subcategory": "Groceries", "description": "Lidl shop",
                         "amount": 10.0, "currency": "EUR", "amount_eur": 10.0})
    db_add_expense(uid, {"date": date(2025, 6, 20), "category": "Transport",
                         "description": "bus ticket", "amount": 2.5,
                         "currency": "EUR", "amount_eur": 2.5})
    from db import add_income
    add_income(uid, {"date": date(2025, 6, 1), "source": "Salary",
                     "income_type": "Salary", "budgeted": 100.0, "actual": 100.0,
                     "currency": "EUR", "budgeted_eur": 100.0,
                     "actual_eur": 100.0, "notes": ""})


def run(coro):
    return asyncio.run(coro)


# ── Parsing & resolution ──────────────────────────────────────────────────────

def test_month_bounds():
    today = date.today()
    first, nxt = mcp._month_bounds("current")
    assert first == today.replace(day=1) and (nxt - first).days >= 28
    first, nxt = mcp._month_bounds("2025-06")
    assert first == date(2025, 6, 1) and nxt == date(2025, 7, 1)
    with pytest.raises(ValueError):
        mcp._month_bounds("june")
    with pytest.raises(ValueError):
        mcp._month_bounds("2025-13")


def test_parse_date():
    assert mcp._parse_date(None) == date.today()
    assert mcp._parse_date("today") == date.today()
    assert mcp._parse_date("yesterday") == date.today() - timedelta(days=1)
    assert mcp._parse_date("2025-06-01") == date(2025, 6, 1)
    with pytest.raises(ValueError):
        mcp._parse_date("01.06.2025")


def test_resolve_user_env_and_default(test_user, monkeypatch):
    monkeypatch.setattr(mcp, "_USER_ID", None)
    assert mcp._resolve_user() == test_user
    monkeypatch.setattr(mcp, "_USER_ID", None)
    monkeypatch.setattr(mcp, "MCP_USERNAME", TEST_USERNAME)
    assert mcp._resolve_user() == test_user
    monkeypatch.setattr(mcp, "_USER_ID", None)
    monkeypatch.setattr(mcp, "MCP_USERNAME", "no_such_user_xyz")
    with pytest.raises(RuntimeError, match="No account"):
        mcp._resolve_user()


# ── Read tools ────────────────────────────────────────────────────────────────

def test_summary_and_lists(test_user):
    _seed(test_user)
    s = run(mcp._expense_summary_impl("2025-06"))
    assert s["ok"] is True
    assert s["spent_eur"] == 12.5 and s["income_eur"] == 100.0
    assert s["net_eur"] == 87.5
    assert s["top_category"]["category"] == "Groceries"

    exp = run(mcp._list_expenses_impl("2025-06", None, 50))
    assert exp["ok"] and exp["count"] == 2 and exp["total_eur"] == 12.5
    cats = {r["category"] for r in exp["expenses"]}
    assert cats == {"Groceries", "Transport"}

    only = run(mcp._list_expenses_impl("2025-06", "transport", 50))
    assert only["count"] == 1 and only["expenses"][0]["description"] == "bus ticket"

    found = run(mcp._search_expenses_impl("lidl", 10))
    assert found["count"] == 1 and found["expenses"][0]["amount_eur"] == 10.0
    none = run(mcp._search_expenses_impl("zzz-no-match", 10))
    assert none["count"] == 0

    inc = run(mcp._list_income_impl("2025-06"))
    assert inc["count"] == 1 and inc["total_eur"] == 100.0
    assert inc["income"][0]["income_type"] == "Salary"


def test_budgets_savings_bills_loans_milestones(test_user):
    from db import add_budget
    add_budget(test_user, {"year": 2025, "month": 6, "category": "Groceries",
                           "subcategory": "", "budgeted_eur": 200.0})
    b = run(mcp._list_budgets_impl())
    assert b["ok"] and b["count"] == 1 and b["budgets"][0]["budgeted_eur"] == 200.0

    sv = run(mcp._list_savings_goals_impl())
    assert sv["ok"] and isinstance(sv["goals"], list)
    assert isinstance(sv["term_deposits"], list)

    for coro in (mcp._list_recurring_bills_impl(), mcp._list_loans_impl(),
                 mcp._get_milestones_impl()):
        res = run(coro)
        assert res["ok"] is True


def test_insights_with_budget(test_user):
    db_add_expense(test_user, {"date": date.today(), "category": "Groceries",
                               "description": "today's shop", "amount": 20.0,
                               "currency": "EUR", "amount_eur": 20.0})
    save_settings(test_user, {"monthly_budget": 1000.0})
    out = run(mcp._get_insights_impl())
    assert out["ok"] is True
    assert "current" in out["spending_mom"]
    assert out["days_until_budget_depleted"] is not None


def test_savings_goals_lists_term_deposits_with_real_fields(test_user):
    # Regression: the tool once referenced non-existent columns
    # (account_name/bank/interest_rate_pct) so term deposits came back empty
    # of their name and rate.
    from db import add_savings_account
    add_savings_account(test_user, {"goal_name": "House", "name": "CD X",
                                    "amount": 1000.0, "currency": "EUR",
                                    "amount_eur": 1000.0, "annual_rate": 4.0,
                                    "start_date": date(2025, 1, 1),
                                    "maturity_date": date(2026, 1, 1),
                                    "status": "active", "notes": ""})
    sv = run(mcp._list_savings_goals_impl())
    assert sv["ok"] is True
    assert sv["term_deposits"], "term deposit must be listed"
    t = sv["term_deposits"][0]
    assert t["name"] == "CD X"
    assert t["annual_rate"] == 4.0
    assert t["goal_name"] == "House"


# ── Write tools ───────────────────────────────────────────────────────────────

def test_add_expense_valid_and_invalid(test_user):
    rev0 = get_data_revision(test_user)
    res = run(mcp._add_expense_impl(
        25.0, "Dining Out", "MCP lunch", date_str="2025-07-03",
        subcategory="Restaurants & Takeaway", currency="EUR"))
    assert res["ok"] is True
    assert res["id"]
    assert get_data_revision(test_user) > rev0

    df = get_expenses(test_user)
    row = df[df["id"] == res["id"]].iloc[0]
    assert row["amount_eur"] == 25.0 and row["description"] == "MCP lunch"
    assert row["category"] == "Dining Out"
    assert row["subcategory"] == "Restaurants & Takeaway"

    # Audit trail marks the MCP origin.
    from db import get_audit_log
    audit = get_audit_log(test_user, limit=10)

    def _has_mcp_via(x):
        d = x if isinstance(x, dict) else (json.loads(x) if isinstance(x, str) else None)
        return isinstance(d, dict) and d.get("via") == "mcp"

    assert any(_has_mcp_via(v) for v in audit["details"].tolist())

    # Rejections are clean errors, not exceptions.
    for kwargs, msg in [
        (dict(amount=0, category="Groceries", description="x"), "amount"),
        (dict(amount=-5, category="Groceries", description="x"), "amount"),
        (dict(amount=True, category="Groceries", description="x"), "number"),
        (dict(amount="5", category="Groceries", description="x"), "number"),
        (dict(amount=5, category="Nonsense", description="x"), "category"),
        (dict(amount=5, category="Groceries", description="x",
              subcategory="Nope"), "subcategory"),
        (dict(amount=5, category="Groceries", description="x",
              currency="XXX"), "currency"),
        (dict(amount=5, category="Groceries", description="  "),
         "description"),
        (dict(amount=5, category="Groceries", description="x" * 501),
         "description"),
    ]:
        r = run(mcp._add_expense_impl(**kwargs))
        assert r["ok"] is False and msg in r["error"], (kwargs, r)


def test_add_expense_converts_currency(test_user):
    save_settings(test_user, {"currency_rates": {"USD": 1.1}})
    res = run(mcp._add_expense_impl(11.0, "Groceries", "USD coffee",
                                    currency="USD"))
    assert res["ok"] is True
    row = get_expenses(test_user)[get_expenses(test_user)["id"] == res["id"]].iloc[0]
    assert row["amount"] == 11.0 and abs(row["amount_eur"] - 10.0) < 0.01


def test_add_income_valid_and_invalid(test_user):
    res = run(mcp._add_income_impl(500.0, "Freelance", date_str="2025-07-01"))
    assert res["ok"] is True
    df = get_income(test_user)
    row = df[df["id"] == res["id"]].iloc[0]
    assert row["actual_eur"] == 500.0 and row["income_type"] == "Freelance"

    bad = run(mcp._add_income_impl(-1, "Other"))
    assert bad["ok"] is False and "amount" in bad["error"]
    bad = run(mcp._add_income_impl(10, "Royalties"))
    assert bad["ok"] is False and "income_type" in bad["error"]
    bad = run(mcp._add_income_impl(True, "Other"))
    assert bad["ok"] is False and "number" in bad["error"]
    bad = run(mcp._add_income_impl(10, "Other", notes="n" * 2001))
    assert bad["ok"] is False and "notes" in bad["error"]


def test_read_tools_return_errors_not_exceptions(test_user, monkeypatch):
    # The REGISTERED tools (what an MCP client invokes) must convert failures
    # into {"ok": False, "error": ...} results instead of raising.
    monkeypatch.setattr(mcp, "_USER_ID", None)
    monkeypatch.setattr(mcp, "MCP_USERNAME", "missing_user_zz")
    res = run(mcp.expense_summary("current"))
    assert res["ok"] is False and "error" in res


def test_ask_data_tool(monkeypatch):
    # No provider configured → clean error result, no exception.
    monkeypatch.setattr(mcp, "_USER_ID", 1)
    res = run(mcp._ask_data_impl("how much did I spend?"))
    assert res["ok"] is False and "not configured" in res["error"]

    # Provider configured + engine works → the answer comes through.
    import llm as llm_module
    monkeypatch.setattr(llm_module, "resolve_provider", lambda s: "api")
    monkeypatch.setattr(llm_module, "answer_query",
                        lambda uid, q, settings, history=None: "123 EUR.")
    res = run(mcp._ask_data_impl("how much did I spend?"))
    assert res == {"ok": True, "answer": "123 EUR."}

    # Engine failure → clean error result.
    monkeypatch.setattr(llm_module, "answer_query",
                        lambda uid, q, settings, history=None: None)
    res = run(mcp._ask_data_impl("how much did I spend?"))
    assert res["ok"] is False and "could not answer" in res["error"]
