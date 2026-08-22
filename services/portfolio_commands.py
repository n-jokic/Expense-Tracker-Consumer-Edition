
"""
services/portfolio_commands.py — #15 sell-domain commands (FIFO + tax model).

Same unit-of-work discipline as services/commands.py: one logical user
command = one transaction = one audit group = one revision bump. Pages see
only CommandResult / CommandError - never SQLAlchemy.

Locked financial-model decisions implemented here:

* FIFO: sells consume holding_lots oldest-first; each lot carries its own
  buy-date FX rate, so EUR cost basis survives currency drift.
* One audited income leg per sell (source 'Investment sale', idempotent via
  settlement_ref). Its amount is proceeds-net-of-tax minus the consumed EUR
  basis - which makes the canonical invariant move unallocated by EXACTLY
  proceeds - tax (Accept criterion), positive on gains, negative on losses.
* Tax = max(0, realized gain) * rate: losses clamp the tax to zero.
* Residual quantity below 1e-6 after a sell deletes the holding (and its
  price history and lots); snapshots stay in audit history.
* Unrealized accrual booking is opt-in and deduped per (user, symbol, year)
  via the income settlement_ref.
"""

from __future__ import annotations

from datetime import date as _date

from .commands import (
    CommandError,
    CommandResult,
    _bump,
    _finite_amount,
    _q2,
    _session,
)

# Country presets for the realized-gains default rate / exemption display.
TAX_PRESETS: dict = {
    "none": {"realized_default_rate": 0.0, "exemption_eur": 0.0},
    "DE":   {"realized_default_rate": 0.26375, "exemption_eur": 1000.0},
    "NL":   {"realized_default_rate": 0.056, "exemption_eur": 0.0},
}

#: Below this residual quantity a holding is considered fully sold.
LOT_EPS = 1e-6


def get_tax_model(user_id: int) -> dict:
    """User's tax-model settings with defaults merged in."""
    import db as _db

    raw = (_db.get_settings(user_id) or {}).get("tax_model") or {}
    model = {"country": "none",
             "realized_default_rate": TAX_PRESETS["none"]["realized_default_rate"],
             "exemption_eur": TAX_PRESETS["none"]["exemption_eur"]}
    if isinstance(raw, dict):
        model.update({k: v for k, v in raw.items() if k in model})
    return model


def save_tax_model(user_id: int, patch: dict) -> CommandResult:
    """Merge user-editable tax-model fields into settings (audited bump)."""
    import db as _db

    clean = {k: patch[k] for k in
             ("country", "realized_default_rate", "exemption_eur")
             if k in patch}
    current = get_tax_model(user_id)
    current.update(clean)
    _db.save_settings(user_id, {"tax_model": current})
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=("tax_model",))


def _holding_fx(h) -> float:
    """1 EUR = X holding-currency, derived from stored basis (fallback 1)."""
    try:
        ce, ct = float(h.cost_eur or 0.0), float(h.cost_total or 0.0)
        if ce > 0 and ct > 0:
            return ct / ce
    except (TypeError, ValueError):
        pass
    return 1.0


def sell_holding_units(user_id: int, holding_id: str, qty: float,
                       price_per_unit: float, *, sell_date=None,
                       tax_rate=None) -> CommandResult:
    """Sell qty units at price_per_unit (holding currency), FIFO oldest-first.

    Legs (one txn): consume lots, shrink/delete the holding, book ONE income
    row for (net proceeds - consumed EUR basis). Raises CommandError on
    oversell, non-positive price or a stale quote (>10% off the stored last
    price when one exists).
    """
    import db as _db
    from db import Holding, HoldingLot, HoldingPrice, Income, log_audit

    qty = round(_finite_amount(qty, "Quantity"), 4)
    price = _finite_amount(price_per_unit, "Price")
    if qty <= 0:
        raise CommandError("quantity must be greater than 0")
    if price <= 0:
        raise CommandError("price must be greater than 0")
    day = sell_date or _date.today()
    ref = "sell:" + str(holding_id) + ":" + str(day) + ":" + str(qty)

    s = _session()
    try:
        existing = s.query(Income).filter(
            Income.settlement_ref == ref,
            Income.is_deleted.isnot(True)).first()
        if existing is not None:
            s.rollback()
            return CommandResult(changed=False, revision=None,
                                 affected_ids=(str(holding_id),))

        h = s.query(Holding).filter(
            Holding.id == str(holding_id),
            Holding.user_id == user_id).first()
        if h is None:
            raise CommandError("holding not found")

        held = float(h.quantity or 0.0)
        if qty > held + 1e-9:
            raise CommandError("cannot sell " + str(qty)
                               + " - you hold " + str(round(held, 4)))
        last = float(h.last_price or 0.0)
        if last > 0 and abs(price - last) / last > 0.10:
            raise CommandError(
                "stale price: your saved quote is "
                + format(last, ",.4f") + " - refresh prices first or use "
                "the current quote")

        _db.ensure_holding_lots_backfilled(user_id)
        lots = (s.query(HoldingLot)
                .filter(HoldingLot.holding_id == str(holding_id),
                        HoldingLot.quantity > LOT_EPS)
                .order_by(HoldingLot.lot_date.asc().nullslast(),
                          HoldingLot.created_at.asc()).all())

        fx_sell = _holding_fx(h)
        remaining = qty
        c_eur = c_orig = 0.0
        for lot in lots:
            if remaining <= LOT_EPS:
                break
            take = min(remaining, float(lot.quantity))
            share = take / float(lot.quantity)
            c_orig += take * float(lot.cost_total) / float(lot.quantity)
            c_eur += take * float(lot.cost_eur) / float(lot.quantity)
            lot.quantity = round(float(lot.quantity) - take, 6)
            lot.cost_total = round(float(lot.cost_total) * (1 - share), 6)
            lot.cost_eur = round(float(lot.cost_eur) * (1 - share), 6)
            if lot.quantity <= LOT_EPS:
                s.delete(lot)
            remaining -= take
        c_eur = _q2(c_eur)

        proceeds_eur = _q2(qty * price / (fx_sell or 1.0))
        rate = (get_tax_model(user_id)["realized_default_rate"]
                if tax_rate is None else max(0.0, min(float(tax_rate), 1.0)))
        gain_eur = proceeds_eur - c_eur
        tax_eur = _q2(max(0.0, gain_eur) * rate)
        leg_eur = _q2(proceeds_eur - tax_eur - c_eur)

        s.add(Income(
            user_id=user_id, date=day, source="Investment sale",
            income_type="Investment", budgeted=leg_eur, actual=leg_eur,
            currency="EUR", budgeted_eur=leg_eur, actual_eur=leg_eur,
            notes=("Sold " + str(qty) + " " + str(h.symbol) + " @ "
                   + format(price, ",.4f") + " - proceeds "
                   + format(proceeds_eur, ",.2f") + " - tax "
                   + format(tax_eur, ",.2f") + " - basis "
                   + format(c_eur, ",.2f")),
            settlement_ref=ref))

        new_qty = round(held - qty, 6)
        if new_qty <= LOT_EPS:
            s.query(HoldingPrice).filter(
                HoldingPrice.holding_id == str(holding_id)).delete()
            s.query(HoldingLot).filter(
                HoldingLot.holding_id == str(holding_id)).delete()
            log_audit(s, user_id, "DELETE", "holdings", str(holding_id),
                      {"fully_sold": True})
            s.delete(h)
        else:
            h.quantity = new_qty
            h.cost_eur = round(float(h.cost_eur or 0.0) - c_eur, 6)
            h.cost_total = round(float(h.cost_total or 0.0) - c_orig, 6)
            log_audit(s, user_id, "UPDATE", "holdings", str(holding_id),
                      {"sold_qty": qty, "price": price,
                       "basis_eur_removed": c_eur,
                       "tax_eur": tax_eur, "ref": ref})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=(str(holding_id),))


def unrealized_accrual_eur(user_id: int, rate=None) -> float:
    """Projected annual accrual: SUM(max(0, value - cost) * rate).

    Simplified preset model - ignores Vorabpauschale basis mechanics
    (documented upgrade path)."""
    import db as _db

    rate = (get_tax_model(user_id)["realized_default_rate"]
            if rate is None else max(0.0, float(rate)))
    total = 0.0
    for h in _db.get_holdings(user_id).itertuples(index=False):
        cost = float(getattr(h, "cost_eur", 0.0) or 0.0)
        fx = _holding_fx(h) or 1.0
        value = (float(getattr(h, "quantity", 0.0) or 0.0)
                 * float(getattr(h, "last_price", 0.0) or 0.0) / fx)
        total += max(0.0, value - cost) * rate
    return round(total, 2)


def book_unrealized_tax_accrual(user_id: int, year: int, *,
                                rate=None) -> CommandResult:
    """Opt-in: book this year's projected accrual per holding, once.

    Deduped per (user, symbol, year) via settlement_ref; nudges each
    holding's EUR cost basis up by its booked accrual."""
    from db import Holding, Income, log_audit

    year = int(year)
    rate = (get_tax_model(user_id)["realized_default_rate"]
            if rate is None else max(0.0, float(rate)))
    s = _session()
    booked = []
    try:
        holdings = s.query(Holding).filter(
            Holding.user_id == user_id).all()
        for h in holdings:
            ref = "accrual:" + str(h.id) + ":" + str(year)
            dup = s.query(Income).filter(
                Income.settlement_ref == ref,
                Income.is_deleted.isnot(True)).first()
            if dup is not None:
                continue
            fx = _holding_fx(h) or 1.0
            value = (float(h.quantity or 0.0)
                     * float(h.last_price or 0.0) / fx)
            accrual = _q2(max(0.0, value - float(h.cost_eur or 0.0)) * rate)
            if accrual <= 0:
                continue
            s.add(Income(
                user_id=user_id, date=_date(year, 12, 31),
                source="Holding accrual " + str(h.symbol) + " " + str(year),
                income_type="Investment", budgeted=accrual, actual=accrual,
                currency="EUR", budgeted_eur=accrual, actual_eur=accrual,
                notes=("Opt-in unrealized-tax accrual for " + str(year)
                       + " (basis nudge)"),
                settlement_ref=ref))
            h.cost_eur = round(float(h.cost_eur or 0.0) + accrual, 6)
            h.cost_total = round(float(h.cost_total or 0.0) + accrual * fx, 6)
            log_audit(s, user_id, "UPDATE", "holdings", str(h.id),
                      {"accrual_year": year, "accrual_eur": accrual,
                       "ref": ref})
            booked.append(str(h.symbol))
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    if not booked:
        return CommandResult(changed=False, revision=None, affected_ids=())
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=tuple(booked))
