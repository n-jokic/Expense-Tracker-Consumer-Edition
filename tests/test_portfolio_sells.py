"""#15 — FIFO sells + tax model: lots, legs, invariant, accrual idempotency."""
from datetime import date, timedelta

import pytest

import db
from auth import hash_password
from db import (add_holding, add_holding_lot, create_user,
                delete_user_account, delete_holding, ensure_holding_lots_backfilled,
                get_holdings, get_holding_lots, get_income, get_user_by_username,
                init_db, username_exists)
from services.commands import CommandError
from services.finance_queries import unallocated_funds_eur
from services.portfolio_commands import (TAX_PRESETS, book_unrealized_tax_accrual,
                                         get_tax_model, save_tax_model,
                                         sell_holding_units,
                                         unrealized_accrual_eur)

U = "sell_user"
E = "sell@example.com"


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Sell Tester")
    yield uid
    delete_user_account(uid)


def _holding(uid, qty=10.0, cost=1000.0, symbol="VOO", price=120.0):
    add_holding(uid, {"symbol": symbol, "name": "Test", "quantity": qty,
                      "currency": "EUR", "cost_total": cost,
                      "cost_eur": cost, "last_price": price,
                      "last_price_date": None})
    return str(get_holdings(uid).iloc[-1]["id"])


def test_lazy_backfill_creates_one_initial_lot(user):
    hid = _holding(user)
    assert len(get_holding_lots(user, hid)) == 0      # not touched yet
    created = ensure_holding_lots_backfilled(user)
    assert created == 1
    lots = get_holding_lots(user, hid)
    assert len(lots) == 1
    row = lots.iloc[0]
    assert float(row["quantity"]) == 10.0
    assert float(row["cost_eur"]) == pytest.approx(1000.0)
    # idempotent
    assert ensure_holding_lots_backfilled(user) == 1 - 1


def test_fifo_consumes_oldest_lot_first(user):
    hid = _holding(user)
    ensure_holding_lots_backfilled(user)
    # the backfilled initial lot is dated today -> it is the OLDER lot;
    # the added lot is dated further out and must NOT be touched first.
    add_holding_lot(user, hid, {"lot_date": date.today() + timedelta(days=5),
                                "quantity": 5.0, "cost_total": 400.0,
                                "cost_eur": 400.0, "rate_at_buy": 1.0})
    # keep the holding consistent with its lots (15 units / 1400 EUR basis)
    db.update_holding(user, hid, {"quantity": 15.0, "cost_total": 1400.0,
                                  "cost_eur": 1400.0})
    sell_holding_units(user, hid, 7.0, 120.0,
                       sell_date=date.today(), tax_rate=0.26375)
    lots = get_holding_lots(user, hid).sort_values("lot_date")
    # oldest (initial) lot reduced to 3 units @100 EUR/unit; newest untouched
    assert len(lots) == 2
    older, newer = lots.iloc[0], lots.iloc[-1]
    assert float(older["quantity"]) == pytest.approx(3.0)
    assert float(older["cost_eur"]) == pytest.approx(300.0)
    assert float(newer["quantity"]) == pytest.approx(5.0)
    assert float(newer["cost_eur"]) == pytest.approx(400.0)
    h = get_holdings(user).iloc[0]
    assert float(h["quantity"]) == pytest.approx(8.0)
    assert float(h["cost_eur"]) == pytest.approx(700.0)


def test_sell_gain_leg_and_invariant(user):
    hid = _holding(user)                       # basis 100 EUR/unit
    db.update_holding(user, hid, {"last_price": 150.0})   # fresh quote
    before = unallocated_funds_eur(user)
    sell_holding_units(user, hid, 4.0, 150.0,       # proceeds 600
                       sell_date=date(2026, 7, 2), tax_rate=0.5)  # tax 50
    after = unallocated_funds_eur(user)
    inc = get_income(user)
    leg = inc[inc["source"] == "Investment sale"].iloc[-1]
    assert float(leg["actual_eur"]) == pytest.approx(600.0 - 200.0 * 0.5
                                                    - 400.0)
    assert after - before == pytest.approx(600.0 - 100.0)  # proceeds - tax


def test_sell_loss_has_zero_tax_and_negative_leg(user):
    hid = _holding(user)
    db.update_holding(user, hid, {"last_price": 30.0})    # fresh quote
    before = unallocated_funds_eur(user)
    sell_holding_units(user, hid, 2.0, 30.0,        # proceeds 60, basis 200
                       sell_date=date(2026, 7, 3), tax_rate=0.25)
    after = unallocated_funds_eur(user)
    leg = get_income(user)
    leg = leg[leg["source"] == "Investment sale"].iloc[-1]
    assert float(leg["actual_eur"]) == pytest.approx(-140.0)
    assert after - before == pytest.approx(60.0)   # proceeds; tax clamped to 0


def test_sell_rejections(user):
    hid = _holding(user, price=120.0)
    with pytest.raises(CommandError):
        sell_holding_units(user, hid, 1.0, 0.0)          # zero price
    with pytest.raises(CommandError):
        sell_holding_units(user, hid, 11.0, 120.0)       # oversell
    with pytest.raises(CommandError):
        sell_holding_units(user, hid, 1.0, 300.0)        # stale (>10% off 120)


def test_full_sell_deletes_holding(user):
    hid = _holding(user)
    sell_holding_units(user, hid, 10.0, 120.0,
                       sell_date=date(2026, 7, 4), tax_rate=0.2)
    assert get_holdings(user).empty
    assert len(get_holding_lots(user, hid)) == 0
    # retry is an accepted no-op via settlement_ref dedupe
    res = sell_holding_units(user, hid, 10.0, 120.0,
                             sell_date=date(2026, 7, 4), tax_rate=0.2)
    assert res.changed is False


def test_tax_model_defaults_and_save(user):
    tm = get_tax_model(user)
    assert tm["country"] == "none"
    save_tax_model(user, {"country": "DE",
                          "realized_default_rate":
                          TAX_PRESETS["DE"]["realized_default_rate"]})
    tm = get_tax_model(user)
    assert tm["country"] == "DE"
    assert tm["realized_default_rate"] == pytest.approx(0.26375)
    assert TAX_PRESETS["NL"]["realized_default_rate"] == pytest.approx(0.056)


def test_accrual_projection_and_idempotent_booking(user):
    hid = _holding(user, qty=10.0, cost=1000.0, price=150.0)  # gain 500
    proj = unrealized_accrual_eur(user, rate=0.2)
    assert proj == pytest.approx(100.0)
    r1 = book_unrealized_tax_accrual(user, 2026, rate=0.2)
    assert r1.changed and r1.affected_ids
    r2 = book_unrealized_tax_accrual(user, 2026, rate=0.2)
    assert r2.changed is False                     # deduped per year
    # basis nudge: cost rose by the booked accrual
    h = get_holdings(user).iloc[0]
    assert float(h["cost_eur"]) == pytest.approx(1100.0)


def test_delete_holding_cleans_lots(user):
    hid = _holding(user)
    ensure_holding_lots_backfilled(user)
    delete_holding(user, hid)                      # must not FK-fail
    assert get_holdings(user).empty
