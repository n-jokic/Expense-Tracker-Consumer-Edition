"""
AI-04 slice-1 regression tests — safe chart answers.

Chart values always equal the canonical tool result; invalid specs fall
back to text/table and can never execute code or inject HTML.
"""

from datetime import date as _date

import pytest

from ai.charts import (
    ALLOWED_TYPES,
    validate_chart_spec,
)

ROWS = [{"month": "2025-01", "amount_eur": 100.0},
        {"month": "2025-02", "amount_eur": 250.5},
        {"month": "2025-03", "amount_eur": 75.25}]

GOOD = {"type": "line", "title": "Spending by month",
        "x": "month", "y": "amount_eur"}


# ── Valid specs pass and data is canonical ───────────────────────────────────

def test_valid_line_spec_passes_with_untouched_values():
    spec = validate_chart_spec(GOOD, ROWS)
    assert spec is not None
    assert spec["type"] == "line"
    assert spec["data"] == ROWS            # exact canonical rows, unmodified
    for r in ROWS:                          # every plotted value exists in source
        assert r in spec["data"]


@pytest.mark.parametrize("kind", ALLOWED_TYPES)
def test_all_allowed_types_validate(kind):
    spec = validate_chart_spec({**GOOD, "type": kind}, ROWS)
    assert spec is not None and spec["type"] == kind


# ── Invalid specs are rejected (fall back to text/table) ────────────────────

def test_unknown_type_rejected():
    assert validate_chart_spec({**GOOD, "type": "scatter3d"}, ROWS) is None
    assert validate_chart_spec({**GOOD, "type": "<script>"}, ROWS) is None


def test_non_dict_spec_rejected():
    assert validate_chart_spec("line", ROWS) is None
    assert validate_chart_spec(None, ROWS) is None


def test_unknown_or_expression_fields_rejected():
    assert validate_chart_spec({**GOOD, "y": "nope"}, ROWS) is None
    assert validate_chart_spec({**GOOD, "x": "a; DROP TABLE x"}, ROWS) is None
    assert validate_chart_spec({**GOOD, "y": "__import__"}, ROWS) is None


def test_missing_field_in_any_row_rejected():
    broken = ROWS + [{"month": "2025-04"}]           # amount_eur missing
    assert validate_chart_spec(GOOD, broken) is None


def test_non_numeric_y_rejected():
    broken = [{"month": "2025-01", "amount_eur": "many"}]
    assert validate_chart_spec(GOOD, broken) is None


def test_bool_y_rejected():
    broken = [{"month": "2025-01", "amount_eur": True}]
    assert validate_chart_spec(GOOD, broken) is None


def test_empty_and_oversized_data_rejected():
    assert validate_chart_spec(GOOD, []) is None
    big = [{"month": f"2025-{i % 12 + 1:02d}", "amount_eur": float(i)}
           for i in range(61)]
    assert validate_chart_spec(GOOD, big) is None     # > MAX_DATA_ROWS
    ok = [{"month": f"2025-{i % 12 + 1:02d}", "amount_eur": float(i)}
          for i in range(60)]
    assert validate_chart_spec(GOOD, ok) is not None


# ── Titles cannot carry markup into the UI ──────────────────────────────────

def test_title_html_and_braces_stripped():
    spec = validate_chart_spec(
        {**GOOD, "title": "<b>Hi</b>{injection}<script>x</script>"},
        ROWS)
    assert spec is not None
    t = spec["title"]
    assert "<" not in t and ">" not in t and "{" not in t and "}" not in t


def test_long_title_capped():
    spec = validate_chart_spec({**GOOD, "title": "x" * 500}, ROWS)
    assert spec is not None and len(spec["title"]) <= 120


def test_non_string_title_falls_back_to_default():
    spec = validate_chart_spec({**GOOD, "title": 42}, ROWS)
    assert spec is not None and spec["title"].endswith("chart")


# ── The series tool produces canonical, chartable data ──────────────────────

def test_spending_series_tool_registered_read_only():
    from ai.tool_registry import TOOLS, TOOL_SCHEMAS
    from ai.safety import is_read_only_tool
    assert "spending_series" in TOOLS
    assert is_read_only_tool("spending_series")
    assert TOOL_SCHEMAS["spending_series"]["required"] == []


@pytest.fixture()
def user():
    import db
    from auth import hash_password
    db.init_db()
    name = "ai_charts_user"
    if db.username_exists(name):
        db.delete_user_account(db.get_user_by_username(name)["id"])
    uid = db.create_user(name, "ai_charts@example.com",
                         hash_password("test1234"), "AI Charts Tester")
    yield uid
    db.delete_user_account(uid)


def test_series_end_to_end_builds_validated_chart(user):
    import db

    db.add_expense(user, {
        "date": _date.today(), "category": "Groceries", "subcategory": "",
        "description": "weekly shop", "amount": 50.0,
        "currency": "EUR", "amount_eur": 50.0,
        "recurring": False, "notes": "",
    })
    from ai.tool_registry import TOOLS
    res = TOOLS["spending_series"](user_id=user, months=3)
    series = res["series"]
    assert len(series) == 3
    this_month = f"{_date.today().year:04d}-{_date.today().month:02d}"
    row = [r for r in series if r["month"] == this_month][0]
    assert row["amount_eur"] == pytest.approx(50.0)

    spec = validate_chart_spec(
        {"type": "bar", "title": "Monthly spending", "x": "month",
         "y": "amount_eur"}, series)
    assert spec is not None
    plotted_total = sum(r["amount_eur"] for r in spec["data"])
    assert plotted_total == pytest.approx(sum(r["amount_eur"] for r in series))
