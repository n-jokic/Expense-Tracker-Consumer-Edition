"""#25 — recurring income templates: salary sync, CRUD, month dedupe,
unlogged detection, forecast-cycle independence."""
from datetime import date, timedelta

import pandas as pd
import pytest

import db
import notifications
from auth import hash_password
from db import (SALARY_TEMPLATE_NAME, add_income_template,
                create_user, delete_income_template,
                delete_user_account, get_income_templates,
                get_settings, get_user_by_username, init_db,
                save_settings, sync_salary_income_template,
                update_income_template, username_exists)

U = "itpl_user"
E = "itpl@example.com"
TODAY = date.today()


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Tpl Tester")
    yield uid
    delete_user_account(uid)


def _activate_salary(uid, amount=3000.0, day=5):
    save_settings(uid, {"salary_active": True, "salary_amount": amount,
                        "salary_currency": "EUR", "salary_day": day})


def test_salary_sync_creates_once_and_updates(user):
    _activate_salary(user)
    assert sync_salary_income_template(user) in (True, False)
    df = get_income_templates(user)
    cards = df[df["description"] == SALARY_TEMPLATE_NAME]
    assert len(cards) == 1
    card = cards.iloc[0]
    assert bool(card["active"]) is True
    assert int(card["due_day"]) == 5
    assert float(card["amount"]) == pytest.approx(3000.0)
    # a raise syncs into the SAME card, never a duplicate
    save_settings(user, {"salary_amount": 3200.0})
    sync_salary_income_template(user)
    df = get_income_templates(user)
    cards = df[df["description"] == SALARY_TEMPLATE_NAME]
    assert len(cards) == 1
    assert float(cards.iloc[0]["amount"]) == pytest.approx(3200.0)


def test_salary_deactivate_deactivates_card(user):
    _activate_salary(user)
    sync_salary_income_template(user)
    save_settings(user, {"salary_active": False})
    sync_salary_income_template(user)
    card = get_income_templates(user).iloc[0]
    assert bool(card["active"]) is False


def test_crud_roundtrip_and_delete(user):
    tid = add_income_template(user, {"description": "Rent income",
                                     "income_type": "Rental",
                                     "amount": 800.0, "currency": "EUR",
                                     "amount_eur": 800.0, "due_day": 3})
    update_income_template(user, tid, {"amount": 850.0})
    df = get_income_templates(user)
    row = df[df["id"] == tid].iloc[0]
    assert float(row["amount"]) == pytest.approx(850.0)
    assert delete_income_template(user, tid) is True
    assert (get_income_templates(user)["id"] == tid).sum() == 0


def test_unlogged_detection_month_scoped(user):
    tid = add_income_template(user, {"description": "Rent income",
                                     "income_type": "Rental",
                                     "amount": 800.0, "currency": "EUR",
                                     "amount_eur": 800.0, "due_day": 1})
    tpls = get_income_templates(user)
    empty_inc = pd.DataFrame(columns=["date", "description", "actual_eur",
                                      "template_id"])
    unlogged = notifications._unlogged_income_templates(tpls, empty_inc, TODAY)
    assert [str(r["id"]) for r in unlogged] == [tid]
    # logged THIS month via template link -> no longer unlogged
    logged = pd.DataFrame([{
        "date": pd.Timestamp(TODAY), "description": "Rent income",
        "actual_eur": 800.0, "template_id": tid}])
    assert notifications._unlogged_income_templates(
        tpls, logged, TODAY) == []
    # last month's log does not satisfy this month
    stale = pd.DataFrame([{
        "date": pd.Timestamp(TODAY - timedelta(days=40)),
        "description": "Rent income", "actual_eur": 800.0,
        "template_id": tid}])
    assert len(notifications._unlogged_income_templates(
        tpls, stale, TODAY)) == 1


def test_add_income_maps_template_fields(user):
    add_income_template(user, {"description": "Rent income",
                               "income_type": "Rental", "amount": 800.0,
                               "currency": "EUR", "amount_eur": 800.0,
                               "due_day": 1})
    tid = str(get_income_templates(user).iloc[0]["id"])
    ref = f"tpl:{tid}:{TODAY.year}:{TODAY.month}"
    db.add_income(user, {"date": TODAY, "source": "Rent income",
                         "income_type": "Rental", "hours": None,
                         "rate": None, "budgeted": 800.0, "actual": 800.0,
                         "currency": "EUR", "budgeted_eur": 800.0,
                         "actual_eur": 800.0, "notes": "From card",
                         "template_id": tid, "settlement_ref": ref})
    inc = db.get_income(user)
    row = inc[inc["settlement_ref"] == ref].iloc[-1]
    assert str(row["template_id"]) == tid


def test_forecast_cycle_inputs_untouched_by_cards(user):
    """Cards alone must not fabricate Income rows (forecast reads Income)."""
    _activate_salary(user)
    sync_salary_income_template(user)
    inc = db.get_income(user)
    assert inc.empty or (inc["source"] != SALARY_TEMPLATE_NAME).all()
