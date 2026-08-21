"""
services/commands.py — Unit-of-Work mutation services (Streamlit-free).

One logical user command = one transaction = one audit group = one revision
bump. Return CommandResult so pages know nothing about SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    changed: bool
    revision: int | None
    affected_ids: tuple[str, ...]
    rejected: int = 0


@dataclass(frozen=True)
class ItemMove:
    id: str
    group: str  # e.g. category name
    position: int


def _bump(user_id: int) -> int:
    from db import bump_data_revision
    return int(bump_data_revision(user_id))


def reorder_recurring_items(user_id: int, moves: list[ItemMove]) -> CommandResult:
    """Reorder recurring templates (+ optional category moves) in one transaction."""
    if not moves:
        return CommandResult(changed=False, revision=None, affected_ids=())
    from db import get_engine, Recurring, log_audit
    from sqlalchemy.orm import sessionmaker
    from domain.taxonomy import CATEGORIES
    engine = get_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    affected: list[str] = []
    try:
        for mv in moves:
            obj = s.query(Recurring).filter(
                Recurring.id == mv.id, Recurring.user_id == user_id).first()
            if obj is None:
                continue
            updates: dict = {"sort_order": int(mv.position)}
            if str(obj.category) != str(mv.group):
                updates["category"] = str(mv.group)
                cur_sub = str(getattr(obj, "subcategory", "") or "")
                if cur_sub and cur_sub not in CATEGORIES.get(mv.group, []):
                    updates["subcategory"] = ""
            changed = False
            for k, v in updates.items():
                if hasattr(obj, k) and getattr(obj, k) != v:
                    setattr(obj, k, v)
                    changed = True
                elif hasattr(obj, k) and getattr(obj, k) == v and k == "sort_order":
                    # sort_order still counts as needing persist if object dirty tracking differs
                    pass
            # Always persist sort_order; apply category/subcategory if needed
            for k, v in updates.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            log_audit(s, user_id, "UPDATE", "recurring", mv.id, updates)
            affected.append(mv.id)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev, affected_ids=tuple(affected))


def reorder_big_purchases(user_id: int, moves: list[ItemMove]) -> CommandResult:
    if not moves:
        return CommandResult(changed=False, revision=None, affected_ids=())
    from db import get_engine, BigPurchase, log_audit
    from sqlalchemy.orm import sessionmaker
    engine = get_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    affected: list[str] = []
    try:
        for mv in moves:
            obj = s.query(BigPurchase).filter(
                BigPurchase.id == mv.id, BigPurchase.user_id == user_id).first()
            if obj is None:
                continue
            updates: dict = {"sort_order": int(mv.position)}
            if str(obj.category) != str(mv.group):
                updates["category"] = str(mv.group)
            for k, v in updates.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            log_audit(s, user_id, "UPDATE", "big_purchases", mv.id, updates)
            affected.append(mv.id)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev, affected_ids=tuple(affected))


def _is_valid_expense_numeric(obj, k, v) -> bool:
    """Reject non-finite / out-of-range floats for money-style columns.

    Guards the shared expense ledger against inf/NaN poisoning: any float-typed
    field that the batch editor surfaces must be finite and within the money
    cap before it is written to a SQLite REAL column (otherwise every downstream
    .sum() becomes inf/NaN).
    """
    from math import isfinite
    from utils import MAX_AMOUNT
    if not hasattr(obj, k):
        return False
    cur = getattr(obj, k, None)
    # Only validate columns whose current stored value is numeric (float/int);
    # this scopes the guard to amount / amount_eur / loan_surcharge_eur on
    # Expense without affecting string/boolean columns.
    if not isinstance(cur, (int, float)):
        return True
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return isfinite(f) and abs(f) <= MAX_AMOUNT


def bulk_update_expenses(user_id: int, updates: list[dict]) -> CommandResult:
    """Each dict: {"id": str, "fields": dict}. One transaction, one revision bump.

    Rows carrying an invalid (non-finite or over-cap) numeric field are rejected
    (counted in CommandResult.rejected) rather than persisted, so the ledger
    can never be poisoned by inf/NaN amounts surfaced through the batch editor.
    """
    if not updates:
        return CommandResult(changed=False, revision=None, affected_ids=(), rejected=0)
    from db import get_engine, Expense, log_audit
    from sqlalchemy.orm import sessionmaker
    engine = get_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    affected: list[str] = []
    rejected = 0
    try:
        for item in updates:
            eid = str(item.get("id") or "")
            fields = dict(item.get("fields") or {})
            if not eid or not fields:
                continue
            obj = s.query(Expense).filter(Expense.id == eid, Expense.user_id == user_id).first()
            if obj is None:
                continue
            skipped: dict = {}
            for k, v in fields.items():
                if (hasattr(obj, k)
                        and isinstance(getattr(obj, k, None), (int, float))
                        and not _is_valid_expense_numeric(obj, k, v)):
                    # Drop the invalid value so it is never persisted; the row
                    # is still committed for its valid co-edits, but counted as
                    # rejected to surface the problem to the UI.
                    skipped[k] = v
                    continue
                if hasattr(obj, k):
                    setattr(obj, k, v)
            rejected += len(skipped)
            if skipped:
                safe_fields = {k: v for k, v in fields.items() if k not in skipped}
            else:
                safe_fields = fields
            if safe_fields:
                log_audit(s, user_id, "UPDATE", "expenses", eid, safe_fields)
            affected.append(eid)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev, affected_ids=tuple(affected), rejected=rejected)


def bulk_soft_delete_expenses(user_id: int, ids: list[str]) -> CommandResult:
    if not ids:
        return CommandResult(changed=False, revision=None, affected_ids=())
    from datetime import datetime, timezone
    from db import get_engine, Expense, log_audit
    from sqlalchemy.orm import sessionmaker
    engine = get_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    affected: list[str] = []
    now = datetime.now(timezone.utc)
    try:
        for eid in ids:
            obj = s.query(Expense).filter(Expense.id == str(eid), Expense.user_id == user_id).first()
            if obj is None:
                continue
            obj.is_deleted = True
            obj.deleted_at = now
            log_audit(s, user_id, "DELETE", "expenses", str(eid), {"soft": True})
            affected.append(str(eid))
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev, affected_ids=tuple(affected))


# ── FIN-04: zero-sum atomic savings / term-account commands ──────────────────
#
# Every money movement here is ONE transaction writing ALL of its legs:
#   * deposits/withdrawals validate against the canonical unallocated-funds
#     invariant / posted goal principal BEFORE anything is written;
#   * opening a term account debits its goal exactly once (zero-sum);
#   * settling a term account credits principal + realized interest exactly
#     once and books the interest as an income row in the same transaction;
#   * idempotency is enforced by partial unique indexes (db: accrual_key /
#     settlement_ref) plus in-transaction pre-checks.
# Rejections raise CommandError subclasses — pages catch and show st.error;
# nothing is silently clamped or swallowed.


class CommandError(Exception):
    """A user command was rejected before any write happened."""


class InsufficientFunds(CommandError):
    """Requested amount exceeds the available pool / spendable principal."""


def _q2(v) -> float:
    """Quantize to cents (half-up)."""
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(float(v))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _finite_amount(v, name: str) -> float:
    from math import isfinite
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise CommandError(f"{name} must be a number.")
    if not isfinite(f):
        raise CommandError(f"{name} must be a finite number.")
    return f


def _unallocated_eur_in_session(s, user_id: int) -> float:
    """Canonical invariant computed INSIDE the command transaction (ORM sums),
    so validation and write see the same snapshot."""
    import sqlalchemy as sa
    from decimal import Decimal
    from db import Income, Expense, Savings, SavingsAccount, Loan, Holding

    def _sum(model, col):
        val = s.query(sa.func.coalesce(sa.func.sum(getattr(model, col)), 0.0)).filter(
            getattr(model, "user_id") == user_id,
            getattr(model, "is_deleted").isnot(True)).scalar()
        return Decimal(str(float(val or 0.0)))

    inflows = _sum(Income, "actual_eur")
    outflows = _sum(Expense, "amount_eur")
    sav_alloc = _sum(Savings, "deposited_eur")
    term_alloc = Decimal(str(float(s.query(
        sa.func.coalesce(sa.func.sum(SavingsAccount.amount_eur), 0.0))
        .filter(SavingsAccount.user_id == user_id,
                SavingsAccount.is_deleted.isnot(True),
                SavingsAccount.status != "closed")
        .scalar() or 0.0)))
    hold_alloc = Decimal(str(float(s.query(
        sa.func.coalesce(sa.func.sum(Holding.cost_eur), 0.0))
        .filter(Holding.user_id == user_id)
        .scalar() or 0.0)))
    financing = Decimal(str(float(s.query(
        sa.func.coalesce(sa.func.sum(Loan.principal_eur), 0.0))
        .filter(Loan.user_id == user_id)
        .scalar() or 0.0)))
    total = inflows + financing - outflows - (sav_alloc + term_alloc + hold_alloc)
    return float(total.quantize(Decimal("0.01")))


def _goal_principal_eur(s, user_id: int, goal_name: str) -> float:
    """Posted principal of one goal = net sum of its non-deleted deposits."""
    import sqlalchemy as sa
    from db import Savings
    val = s.query(sa.func.coalesce(sa.func.sum(Savings.deposited_eur), 0.0)).filter(
        Savings.user_id == user_id,
        Savings.goal_name == goal_name,
        Savings.is_deleted.isnot(True)).scalar()
    return round(float(val or 0.0), 2)


def _session():
    from db import get_engine
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=get_engine(), expire_on_commit=False)()


def deposit_to_goal(user_id: int, goal_name: str, amount_eur: float, *,
                    entry_date=None, target_eur: float = 0.0,
                    deposited: float | None = None, currency: str = "EUR",
                    interest_rate: float = 0.0, notes: str = "") -> CommandResult:
    """Move money from unallocated funds into a goal (one transaction).

    Rejects InsufficientFunds when amount exceeds the canonical unallocated
    pool. amount_eur is quantized to cents before validation."""
    from datetime import date as _date
    from db import Savings, log_audit
    amount = _q2(_finite_amount(amount_eur, "Amount"))
    if amount <= 0:
        raise CommandError("Deposit must be greater than 0.")
    s = _session()
    try:
        available = _unallocated_eur_in_session(s, user_id)
        if amount > available + 0.005:
            raise InsufficientFunds(
                f"Insufficient funds: {amount:.2f} € requested, "
                f"{available:.2f} € unallocated.")
        row = Savings(
            user_id=user_id, goal_name=goal_name,
            date=entry_date or _date.today(),
            target_eur=float(target_eur or 0.0),
            deposited=float(deposited if deposited is not None else amount),
            currency=currency, deposited_eur=amount,
            interest_rate=float(interest_rate or 0.0),
            balance_eur=0.0, notes=notes or "",
        )
        s.add(row)
        s.flush()
        rid = row.id
        log_audit(s, user_id, "CREATE", "savings", rid,
                  {"goal": goal_name, "deposited_eur": amount})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id), affected_ids=(rid,))


def withdraw_from_goal(user_id: int, goal_name: str, amount_eur: float, *,
                       entry_date=None, target_eur: float = 0.0,
                       currency: str = "EUR", interest_rate: float | None = None,
                       notes: str = "") -> CommandResult:
    """Move money from a goal back to unallocated funds (negative deposit row).

    Rejects when amount exceeds the goal's POSTED principal (deposits minus
    withdrawals; posted interest credits count once they exist). No clamp:
    overdrafts are prevented by this validation, not masked on read.
    interest_rate defaults to the goal's current rate so the accrual chain
    keeps its earning configuration across withdrawals."""
    from datetime import date as _date
    from db import Savings, log_audit
    amount = _q2(_finite_amount(amount_eur, "Amount"))
    if amount <= 0:
        raise CommandError("Withdrawal must be greater than 0.")
    s = _session()
    try:
        principal = _goal_principal_eur(s, user_id, goal_name)
        if amount > principal + 0.005:
            raise InsufficientFunds(
                f"Withdrawal exceeds spendable goal principal: "
                f"{amount:.2f} € requested, {principal:.2f} € available.")
        if interest_rate is None:
            last = (s.query(Savings.interest_rate)
                    .filter(Savings.user_id == user_id,
                            Savings.goal_name == goal_name,
                            Savings.is_deleted.isnot(True),
                            Savings.deposited_eur > 0)
                    .order_by(Savings.date.desc(), Savings.created_at.desc())
                    .first())
            rate = float(last[0]) if last and last[0] is not None else 0.0
        else:
            rate = float(interest_rate)
        row = Savings(
            user_id=user_id, goal_name=goal_name,
            date=entry_date or _date.today(),
            target_eur=float(target_eur or 0.0),
            deposited=-amount, currency=currency, deposited_eur=-amount,
            interest_rate=rate,
            balance_eur=0.0, notes=notes or "Withdrawal",
        )
        s.add(row)
        s.flush()
        rid = row.id
        log_audit(s, user_id, "CREATE", "savings", rid,
                  {"goal": goal_name, "withdrawn_eur": amount})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id), affected_ids=(rid,))


def open_term_from_goal(user_id: int, goal_name: str, name: str,
                        amount_eur: float, annual_rate: float,
                        start_date, maturity_date, *,
                        currency: str = "EUR", amount: float | None = None,
                        target_eur: float = 0.0, interest_rate: float = 0.0,
                        notes: str = "") -> CommandResult:
    """Open a fixed-term deposit funded FROM a goal — zero-sum in one txn.

    Writes BOTH legs atomically: the active SavingsAccount row and the
    matching negative goal debit row. Rejects when the goal principal is too
    small, dates are inverted, or rate/name invalid."""
    from db import Savings, SavingsAccount, log_audit
    if not str(name or "").strip():
        raise CommandError("The term account needs a name.")
    amt = _q2(_finite_amount(amount_eur, "Amount"))
    if amt <= 0:
        raise CommandError("Term amount must be greater than 0.")
    rate = _finite_amount(annual_rate, "Annual rate")
    if rate < 0 or rate > 100:
        raise CommandError("Annual rate must be between 0 and 100.")
    if maturity_date <= start_date:
        raise CommandError("Maturity date must be after the start date.")
    s = _session()
    try:
        principal = _goal_principal_eur(s, user_id, goal_name)
        if amt > principal + 0.005:
            raise InsufficientFunds(
                f"Goal '{goal_name}' holds {principal:.2f} € — "
                f"cannot lock {amt:.2f} € into a term deposit.")
        acc = SavingsAccount(
            user_id=user_id, goal_name=goal_name, name=str(name).strip(),
            amount=float(amount if amount is not None else amt),
            currency=currency, amount_eur=amt, annual_rate=rate,
            start_date=start_date, maturity_date=maturity_date,
            status="active", notes=notes or "",
        )
        s.add(acc)
        s.flush()
        debit = Savings(
            user_id=user_id, goal_name=goal_name,
            date=start_date, target_eur=float(target_eur or 0.0),
            deposited=-amt, currency=currency, deposited_eur=-amt,
            interest_rate=float(interest_rate or 0.0),
            balance_eur=0.0,
            notes=f"Term opened: {str(name).strip()} ({acc.id[:8]})",
        )
        s.add(debit)
        s.flush()
        aid = acc.id
        log_audit(s, user_id, "CREATE", "savings_accounts", aid,
                  {"goal": goal_name, "amount_eur": amt, "annual_rate": rate,
                   "debit_row": debit.id})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id), affected_ids=(aid,))


def settle_term_account(user_id: int, account_id: str, *,
                        realized_interest_eur: float,
                        payout_date=None) -> CommandResult:
    """Withdraw a term deposit into its goal — principal + realized interest,
    exactly once, in ONE transaction.

    Legs (all same txn): credit principal to goal, book realized interest as
    an income row (source 'Term interest'), credit the interest to the goal,
    close the account. Idempotent: a retry finds the settlement_ref rows and
    returns changed=False instead of re-crediting."""
    from datetime import date as _date
    from db import Savings, SavingsAccount, Income, log_audit
    interest = _q2(max(_finite_amount(realized_interest_eur, "Realized interest"), 0.0))
    ref = f"term-settle:{account_id}"
    s = _session()
    try:
        acc = s.query(SavingsAccount).filter(
            SavingsAccount.id == str(account_id),
            SavingsAccount.user_id == user_id).first()
        if acc is None:
            return CommandResult(changed=False, revision=None, affected_ids=())
        existing_income = s.query(Income).filter(
            Income.settlement_ref == ref,
            Income.is_deleted.isnot(True)).first()
        if existing_income is not None or str(acc.status) == "closed":
            # Already settled (crash-retry or double click) — no-op.
            s.rollback()
            return CommandResult(changed=False, revision=None,
                                 affected_ids=(str(account_id),))
        principal = _q2(float(acc.amount_eur or 0.0))
        goal = str(acc.goal_name)
        pay_day = payout_date or _date.today()

        principal_credit = Savings(
            user_id=user_id, goal_name=goal, date=pay_day,
            deposited=principal, currency=str(acc.currency or "EUR"),
            deposited_eur=principal, interest_rate=float(acc.annual_rate or 0.0),
            balance_eur=0.0, notes=f"Term settlement: {acc.name}",
            settlement_ref=f"{ref}:p",
        )
        s.add(principal_credit)
        if interest > 0:
            s.add(Income(
                user_id=user_id, date=pay_day, source="Term interest",
                income_type="Investment", budgeted=interest, actual=interest,
                currency="EUR", budgeted_eur=interest, actual_eur=interest,
                notes=f"Realized interest from {acc.name}", settlement_ref=ref,
            ))
            s.add(Savings(
                user_id=user_id, goal_name=goal, date=pay_day,
                deposited=interest, currency="EUR", deposited_eur=interest,
                interest_rate=float(acc.annual_rate or 0.0),
                balance_eur=0.0, notes=f"Term interest: {acc.name}",
                settlement_ref=f"{ref}:i",
            ))
        acc.status = "closed"
        s.flush()
        log_audit(s, user_id, "UPDATE", "savings_accounts", str(account_id),
                  {"settled": True, "principal_eur": principal,
                   "interest_eur": interest, "ref": ref})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=(str(account_id),))


def post_monthly_interest(user_id: int, *, asof=None) -> CommandResult:
    """Post accrued savings interest for all goals up to `asof` (default:
    end of the previous month) — the locked daily-accrual/monthly-payout model.

    For every goal: pending = ACT/365 daily accrual on the POSTED balance
    since the last event (deposit, withdrawal, or earlier posting). Pending
    ≥ €0.01 books TWO rows in one txn per goal: an income row
    (source 'Savings interest', settlement_ref = accrual marker) and a goal
    credit row (accrual_key = marker). Unique indexes make reruns no-ops."""
    from datetime import date as _date, timedelta
    from decimal import Decimal
    import sqlalchemy as sa
    import finance as fin
    from db import Savings, Income, log_audit
    if asof is None:
        today = _date.today()
        asof = today.replace(day=1) - timedelta(days=1)
    marker_month = f"{asof.year}-{asof.month:02d}"
    s = _session()
    affected: list[str] = []
    try:
        goals = [g for (g,) in s.query(Savings.goal_name).filter(
            Savings.user_id == user_id,
            Savings.is_deleted.isnot(True),
            Savings.goal_name.isnot(None)).distinct().all()]
        for goal in goals:
            marker = f"savings-interest:{goal}:{marker_month}"
            already = s.query(Savings).filter(
                Savings.user_id == user_id,
                Savings.accrual_key == marker).first()
            if already is not None:
                continue
            rows = s.query(Savings.date, Savings.deposited_eur,
                           Savings.interest_rate).filter(
                Savings.user_id == user_id,
                Savings.goal_name == goal,
                Savings.is_deleted.isnot(True)).all()
            timeline_rows = [
                {"date": r[0], "deposited_eur": r[1], "interest_rate": r[2]}
                for r in rows]
            accrued = Decimal(str(fin.goal_balance_timeline(
                timeline_rows, asof=asof)["accrued_interest_eur"]))
            # interest already posted for this goal (credit rows dated ≤ asof)
            posted_val = s.query(
                sa.func.coalesce(sa.func.sum(Savings.deposited_eur), 0.0)).filter(
                Savings.user_id == user_id,
                Savings.goal_name == goal,
                Savings.accrual_key.isnot(None),
                Savings.is_deleted.isnot(True),
                Savings.date <= asof).scalar()
            due = float(accrued - Decimal(str(float(posted_val or 0.0))))
            if abs(due) < 0.01:
                continue
            # goal's current earning rate (latest deposit row) — carried onto
            # the credit row so future accrual keeps its configuration
            last_rate = (s.query(Savings.interest_rate)
                         .filter(Savings.user_id == user_id,
                                 Savings.goal_name == goal,
                                 Savings.is_deleted.isnot(True),
                                 Savings.deposited_eur > 0)
                         .order_by(Savings.date.desc(), Savings.created_at.desc())
                         .first())
            rate = float(last_rate[0]) if last_rate and last_rate[0] is not None else 0.0
            s.add(Income(
                user_id=user_id, date=asof, source="Savings interest",
                income_type="Investment", budgeted=due, actual=due,
                currency="EUR", budgeted_eur=due, actual_eur=due,
                notes=f"Accrued interest {marker_month} — {goal}",
                settlement_ref=marker,
            ))
            credit = Savings(
                user_id=user_id, goal_name=goal, date=asof,
                deposited=due, currency="EUR", deposited_eur=due,
                interest_rate=rate, balance_eur=0.0,
                notes=f"Interest posted {marker_month}", accrual_key=marker,
            )
            s.add(credit)
            s.flush()
            log_audit(s, user_id, "CREATE", "savings", credit.id,
                      {"accrual": marker, "interest_eur": due})
            affected.append(credit.id)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev,
                         affected_ids=tuple(affected))


def soft_delete_goal_checked(user_id: int, goal_name: str) -> CommandResult:
    """Soft-delete a savings goal — blocked while it still holds value.

    A non-empty goal (posted principal ≠ 0 or active term accounts) must be
    withdrawn/settled first: deletion must never silently release or destroy
    allocated money."""
    from datetime import datetime, timezone
    from db import get_engine, Savings, SavingsAccount, log_audit
    from sqlalchemy.orm import sessionmaker
    s = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        principal = _goal_principal_eur(s, user_id, goal_name)
        active_terms = s.query(SavingsAccount).filter(
            SavingsAccount.user_id == user_id,
            SavingsAccount.goal_name == goal_name,
            SavingsAccount.status == "active",
            SavingsAccount.is_deleted.isnot(True)).count()
        if abs(principal) > 0.005 or active_terms:
            raise CommandError(
                f"Goal '{goal_name}' is not empty "
                f"({principal:.2f} € principal, {active_terms} active term "
                "accounts). Withdraw the money or settle the terms first.")
        now = datetime.now(timezone.utc)
        rows = s.query(Savings).filter(
            Savings.user_id == user_id,
            Savings.goal_name == goal_name,
            Savings.is_deleted.isnot(True)).all()
        affected: list[str] = []
        for row in rows:
            row.is_deleted = True
            row.deleted_at = now
            log_audit(s, user_id, "DELETE", "savings", row.id,
                      {"soft": True, "goal_delete": True})
            affected.append(row.id)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev,
                         affected_ids=tuple(affected))


def soft_delete_account_checked(user_id: int, account_id: str) -> CommandResult:
    """Soft-delete a term account — blocked while active with value.

    Settle (withdraw) first: deleting an active account would otherwise make
    its principal vanish from the allocations without returning it anywhere."""
    from datetime import datetime, timezone
    from db import get_engine, SavingsAccount, log_audit
    from sqlalchemy.orm import sessionmaker
    s = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        acc = s.query(SavingsAccount).filter(
            SavingsAccount.id == str(account_id),
            SavingsAccount.user_id == user_id).first()
        if acc is None:
            return CommandResult(changed=False, revision=None, affected_ids=())
        if str(acc.status) == "active" and abs(float(acc.amount_eur or 0.0)) > 0.005:
            raise CommandError(
                f"'{acc.name}' is an active term holding "
                f"{float(acc.amount_eur):.2f} €. Withdraw (settle) it first — "
                "deleting it would lose track of the money.")
        acc.is_deleted = True
        acc.deleted_at = datetime.now(timezone.utc)
        log_audit(s, user_id, "DELETE", "savings_accounts", str(account_id),
                  {"soft": True})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=(str(account_id),))
