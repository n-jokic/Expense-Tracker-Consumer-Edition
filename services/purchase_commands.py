"""
services/purchase_commands.py — FIN-06/FIN-07 purchase-domain commands.

Same unit-of-work discipline as services/commands.py: one logical user
command = one transaction = one audit group = one revision bump. Pages see
only CommandResult / CommandError — never SQLAlchemy.

Locked financial-model decisions implemented here:

* Funding source is explicit (``unallocated | savings_goal``). The optional
  savings-goal link is stored as a STABLE reference — the id of the Savings
  row that anchored the goal — so it survives reload, item edits and goal
  renames (rename rewrites ``goal_name``, never row ids) and degrades
  gracefully when the linked goal later vanishes.
* Buying a wishlist item is ONE atomic transaction: validate funds → debit
  the selected source → create ONE linked expense → stamp the item bought →
  single revision bump. Retries are idempotent via the stable expense
  reference (``BigPurchase.expense_id``); any failure rolls back every leg.
* Refunds are explicit compensating commands that soft-delete the linked
  expense and the funding debit row (history preserved) and restore the
  exact pre-buy status.
* The free status selector must never reach ``bought``: SELECTABLE_STATUSES
  excludes it, so the buy command is the only path to ``bought``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Reuse the shared UoW primitives — no duplicated session/audit/revision code.
from services.commands import (
    CommandError,
    CommandResult,
    InsufficientFunds,
    _bump,
    _finite_amount,
    _goal_principal_eur,
    _q2,
    _session,
    _unallocated_eur_in_session,
)

FUNDING_UNALLOCATED = "unallocated"
FUNDING_SAVINGS_GOAL = "savings_goal"
_FUNDING_SOURCES = (FUNDING_UNALLOCATED, FUNDING_SAVINGS_GOAL)

#: Marker written on the goal-debit Savings row (settlement_ref column, which
#: carries a partial UNIQUE index) — makes the debit leg idempotent at the
#: storage level and findable by the refund command.
def buy_debit_ref(purchase_id: str) -> str:
    return f"wishlist-buy:{purchase_id}"


#: Statuses a user may pick freely; "bought" is reachable ONLY through
#: buy_wishlist_item (FIN-07: no status bypass).
SELECTABLE_STATUSES: tuple[str, ...] = ("wishlist", "saving")


def is_selectable_status(value: str) -> bool:
    return str(value) in SELECTABLE_STATUSES


# ── FIN-06: stable funding reference ─────────────────────────────────────────


def resolve_funding_goal_name_in_session(s, user_id: int, ref: str | None) -> str | None:
    """Resolve the anchor-row reference to the goal's CURRENT name.

    Survives renames because only ``goal_name`` is rewritten. If the anchor
    row itself was soft-deleted but the goal still lives under that name,
    the name still resolves; a truly vanished goal resolves to None."""
    from db import Savings
    if not ref:
        return None
    row = s.query(Savings).filter(
        Savings.id == str(ref), Savings.user_id == user_id).first()
    if row is None:
        return None
    name = str(row.goal_name or "")
    if not name:
        return None
    if not row.is_deleted:
        return name
    alive = s.query(Savings).filter(
        Savings.user_id == user_id,
        Savings.goal_name == name,
        Savings.is_deleted.isnot(True)).first()
    return name if alive is not None else None


def resolve_linked_goal_name(user_id: int, ref: str | None) -> str | None:
    """Session-free variant for pages/tests."""
    s = _session()
    try:
        return resolve_funding_goal_name_in_session(s, user_id, ref)
    finally:
        s.close()


def goal_anchor_ref(s, user_id: int, goal_name: str) -> str | None:
    """Stable anchor for a goal: id of its earliest non-deleted Savings row."""
    from db import Savings
    row = (s.query(Savings)
           .filter(Savings.user_id == user_id,
                   Savings.goal_name == goal_name,
                   Savings.is_deleted.isnot(True))
           .order_by(Savings.date.asc(), Savings.created_at.asc(), Savings.id.asc())
           .first())
    return str(row.id) if row is not None else None


def create_wishlist_target(user_id: int, goal_name: str, *,
                           target_eur: float = 0.0,
                           interest_rate: float = 0.0) -> CommandResult:
    """Create an empty savings target ('create a target' wishlist choice).

    Writes ONE zero-deposit anchor Savings row carrying the target amount —
    not a money movement (deposited_eur = 0 contributes nothing to the
    canonical invariant), so the goal exists and can be linked immediately.
    Returns the anchor row id in affected_ids for direct linking."""
    from datetime import date as _date
    from db import Savings, log_audit
    name = str(goal_name or "").strip()
    if not name:
        raise CommandError("The savings target needs a name.")
    tgt = _q2(max(_finite_amount(target_eur or 0.0, "Target amount"), 0.0))
    rate = _finite_amount(interest_rate or 0.0, "Interest rate")
    if rate < 0 or rate > 100:
        raise CommandError("Interest rate must be between 0 and 100.")
    s = _session()
    try:
        clash = s.query(Savings).filter(
            Savings.user_id == user_id,
            Savings.is_deleted.isnot(True)).all()
        if any(str(r.goal_name or "").strip().lower() == name.lower() for r in clash):
            raise CommandError(f"A savings goal named '{name}' already exists.")
        row = Savings(
            user_id=user_id, goal_name=name, date=_date.today(),
            target_eur=tgt, deposited=0.0, currency="EUR", deposited_eur=0.0,
            interest_rate=rate, balance_eur=0.0,
            notes="Wishlist funding target",
        )
        s.add(row)
        s.flush()
        rid = str(row.id)
        log_audit(s, user_id, "CREATE", "savings", rid,
                  {"goal": name, "target_eur": tgt, "wishlist_target": True})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id), affected_ids=(rid,))


def set_purchase_funding(user_id: int, purchase_id: str, *, source: str | None,
                         goal_ref: str | None = None) -> CommandResult:
    """Attach / change / clear a wishlist item's funding target (FIN-06).

    source=None clears the link; 'unallocated' stores the explicit choice;
    'savings_goal' requires a resolvable goal_ref (anchor row id). Blocked
    for bought items — their funding was consumed by the buy command."""
    from db import BigPurchase, log_audit
    src = str(source).strip() if source else ""
    if src and src not in _FUNDING_SOURCES:
        raise CommandError(f"Unknown funding source '{src}'.")
    s = _session()
    try:
        item = s.query(BigPurchase).filter(
            BigPurchase.id == str(purchase_id),
            BigPurchase.user_id == user_id).first()
        if item is None:
            raise CommandError("Wishlist item not found.")
        if str(item.status) == "bought":
            raise CommandError("This item was already bought — its funding "
                               "cannot be changed anymore.")
        updates: dict = {}
        if src == FUNDING_SAVINGS_GOAL:
            resolved = resolve_funding_goal_name_in_session(s, user_id, goal_ref)
            if resolved is None:
                raise CommandError("The selected savings goal no longer exists.")
            updates = {"funding_source": FUNDING_SAVINGS_GOAL,
                       "funding_goal_ref": str(goal_ref)}
        elif src == FUNDING_UNALLOCATED:
            updates = {"funding_source": FUNDING_UNALLOCATED,
                       "funding_goal_ref": None}
        else:
            updates = {"funding_source": None, "funding_goal_ref": None}
        for k, v in updates.items():
            setattr(item, k, v)
        log_audit(s, user_id, "UPDATE", "big_purchases", str(purchase_id),
                  {"funding": updates})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=(str(purchase_id),))


def funding_summary(purchase: Mapping, goal_rows: Iterable[Mapping]) -> str:
    """Human-readable funding line for a card (Streamlit-free, testable).

    Uses the canonical goal_balance_timeline math on the caller's rows —
    never a duplicated savings calculation. Missing goals render gracefully.
    Returns '' when the item has no funding target."""
    import finance as fin
    src = str(purchase.get("funding_source") or "")
    if src not in _FUNDING_SOURCES:
        return ""
    if src == FUNDING_UNALLOCATED:
        return "Funding: unallocated funds"

    ref = purchase.get("funding_goal_ref")
    rows = [dict(r) if not isinstance(r, dict) else r for r in goal_rows]
    anchor = next((r for r in rows if str(r.get("id")) == str(ref)), None)
    if anchor is None:
        return "⚠️ Linked savings goal no longer exists"
    goal = str(anchor.get("goal_name") or "")
    grows = [r for r in rows if str(r.get("goal_name") or "") == goal]
    bal = fin.goal_balance_timeline(grows)["posted_balance_eur"]
    targets = [float(r.get("target_eur") or 0.0) for r in grows]
    target = max(targets) if targets else 0.0
    if target > 0:
        return f"Funding: {goal} — €{bal:,.2f} of €{target:,.2f} saved"
    return f"Funding: {goal} — €{bal:,.2f} saved"


# ── FIN-07: atomic buy / refund ───────────────────────────────────────────────


def _autoarchive_goal_if_drained(s, user_id: int, goal_name: str,
                                 purchase_id: str) -> bool:
    """B3: soft-delete a goal whose principal this buy fully consumed AND
    that no other unbought wishlist item still links to AND that has no
    active term accounts. Runs INSIDE the caller's transaction so the
    archive commits atomically with the buy."""
    from datetime import datetime, timezone
    from db import AuditLog, BigPurchase, Savings, SavingsAccount, log_audit

    if _goal_principal_eur(s, user_id, goal_name) > 0.005:
        return False
    active_terms = s.query(SavingsAccount).filter(
        SavingsAccount.user_id == user_id,
        SavingsAccount.goal_name == goal_name,
        SavingsAccount.is_deleted.isnot(True),
        SavingsAccount.status == "active").count()
    if active_terms:
        return False
    for other in s.query(BigPurchase).filter(
            BigPurchase.user_id == user_id,
            BigPurchase.funding_goal_ref.isnot(None),
            BigPurchase.id != str(purchase_id),
            BigPurchase.status != "bought").all():
        if resolve_funding_goal_name_in_session(
                s, user_id, other.funding_goal_ref) == goal_name:
            return False
    now = datetime.now(timezone.utc)
    n = na = 0
    for obj in s.query(Savings).filter(
            Savings.user_id == user_id,
            Savings.goal_name == goal_name,
            Savings.is_deleted.isnot(True)).all():
        obj.is_deleted = True
        obj.deleted_at = now
        n += 1
    for obj in s.query(SavingsAccount).filter(
            SavingsAccount.user_id == user_id,
            SavingsAccount.goal_name == goal_name,
            SavingsAccount.is_deleted.isnot(True)).all():
        obj.is_deleted = True
        obj.deleted_at = now
        na += 1
    log_audit(s, user_id, "AUTO_ARCHIVE", "savings_goal", goal_name,
              {"entries_trashed": n, "accounts_trashed": na,
               "purchase_id": str(purchase_id)})
    return True


def _autorestore_goal_if_archived(s, user_id: int, goal_name: str | None,
                                  purchase_id: str) -> bool:
    """B3 mirror of the auto-archive: on refund, bring back a goal ONLY if
    it is fully gone AND an AUTO_ARCHIVE audit exists for it (a goal the
    user deleted manually stays deleted). Same-transaction discipline."""
    from datetime import datetime, timezone
    from db import AuditLog, Savings, SavingsAccount, log_audit
    if not goal_name:
        return False
    live = s.query(Savings).filter(
        Savings.user_id == user_id,
        Savings.goal_name == goal_name,
        Savings.is_deleted.isnot(True)).count()
    if live:
        return False
    last_archive = (s.query(AuditLog)
                    .filter(AuditLog.user_id == user_id,
                            AuditLog.action == "AUTO_ARCHIVE",
                            AuditLog.table_name == "savings_goal",
                            AuditLog.record_id == goal_name)
                    .order_by(AuditLog.timestamp.desc(),
                              AuditLog.id.desc()).first())
    if last_archive is None:
        return False
    now = datetime.now(timezone.utc)
    n = na = 0
    # Pre-buy, this purchase's own debit row did not exist — never resurrect it.
    from sqlalchemy import or_
    own_ref = buy_debit_ref(str(purchase_id))
    for obj in s.query(Savings).filter(
            Savings.user_id == user_id,
            Savings.goal_name == goal_name,
            Savings.is_deleted.is_(True),
            or_(Savings.settlement_ref.is_(None),
                Savings.settlement_ref != own_ref)).all():
        obj.is_deleted = False
        obj.deleted_at = None
        n += 1
    for obj in s.query(SavingsAccount).filter(
            SavingsAccount.user_id == user_id,
            SavingsAccount.goal_name == goal_name,
            SavingsAccount.deleted_at.isnot(None)).all():
        obj.is_deleted = False
        obj.deleted_at = None
        na += 1
    log_audit(s, user_id, "AUTO_RESTORE", "savings_goal", goal_name,
              {"entries_restored": n, "accounts_restored": na,
               "purchase_id": str(purchase_id)})
    return True


def buy_wishlist_item(user_id: int, purchase_id: str, *,
                      entry_date=None, amount_eur: float | None = None,
                      category: str | None = None,
                      notes: str | None = None) -> CommandResult:
    """Buy a wishlist item — ONE transaction, every leg together.

    Legs: validate funds against the canonical pool / posted goal principal
    → (goal-funded) one negative Savings debit row marked with the stable
    ``wishlist-buy:<id>`` settlement_ref → ONE linked Expense row → item
    stamped bought with pre_buy_status + expense_id → single audit group →
    single revision bump.

    Idempotent: a retry finds the item already bought WITH its linked
    expense and returns changed=False instead of duplicating anything.
    Rejections raise CommandError/InsufficientFunds before any write."""
    from datetime import date as _date
    from db import BigPurchase, Expense, Savings, log_audit
    s = _session()
    try:
        item = s.query(BigPurchase).filter(
            BigPurchase.id == str(purchase_id),
            BigPurchase.user_id == user_id).first()
        if item is None:
            raise CommandError("Wishlist item not found.")

        # Idempotent retry / double-buy guard — BEFORE anything is written.
        if str(item.status) == "bought":
            linked = None
            if item.expense_id:
                linked = s.query(Expense).filter(
                    Expense.id == str(item.expense_id),
                    Expense.user_id == user_id,
                    Expense.is_deleted.isnot(True)).first()
            if linked is not None:
                return CommandResult(changed=False, revision=None,
                                     affected_ids=(str(purchase_id),))
            raise CommandError(
                "This item is already marked bought (without a linked "
                "expense). Use refund/revert to change it.")

        amount = _q2(_finite_amount(
            amount_eur if amount_eur is not None else float(item.price_eur or 0.0),
            "Amount"))
        if amount <= 0:
            raise CommandError("The purchase price must be greater than 0.")

        # Explicit funding source; legacy rows without one default to the
        # unallocated pool.
        src = str(item.funding_source or FUNDING_UNALLOCATED)
        if src not in _FUNDING_SOURCES:
            raise CommandError(f"Unknown funding source '{src}' on the item.")
        goal_name = None
        if src == FUNDING_SAVINGS_GOAL:
            goal_name = resolve_funding_goal_name_in_session(
                s, user_id, item.funding_goal_ref)
            if goal_name is None:
                raise CommandError(
                    "The linked savings goal no longer exists — choose "
                    "another funding source before buying.")
            principal = _goal_principal_eur(s, user_id, goal_name)
            if amount > principal + 0.005:
                raise InsufficientFunds(
                    f"Goal '{goal_name}' holds {principal:.2f} € — cannot "
                    f"fund a {amount:.2f} € purchase from it.")
        else:
            available = _unallocated_eur_in_session(s, user_id)
            if amount > available + 0.005:
                raise InsufficientFunds(
                    f"Insufficient funds: {amount:.2f} € requested, "
                    f"{available:.2f} € unallocated.")

        pay_day = entry_date or _date.today()
        exp = Expense(
            user_id=user_id, date=pay_day,
            category=str(category or item.category or "Other"),
            subcategory="",
            description=f"{item.name} (big purchase)",
            amount=float(item.price or 0.0),
            currency=str(item.currency or "EUR"),
            amount_eur=amount, recurring=False,
            notes=str(notes) if notes is not None else "Big purchase",
        )
        s.add(exp)
        s.flush()

        debit_row_id = None
        if src == FUNDING_SAVINGS_GOAL:
            last_rate = (s.query(Savings.interest_rate)
                         .filter(Savings.user_id == user_id,
                                 Savings.goal_name == goal_name,
                                 Savings.is_deleted.isnot(True),
                                 Savings.deposited_eur > 0)
                         .order_by(Savings.date.desc(), Savings.created_at.desc())
                         .first())
            rate = float(last_rate[0]) if last_rate and last_rate[0] is not None else 0.0
            debit = Savings(
                user_id=user_id, goal_name=goal_name, date=pay_day,
                target_eur=0.0, deposited=-amount, currency="EUR",
                deposited_eur=-amount, interest_rate=rate, balance_eur=0.0,
                notes=f"Wishlist purchase: {item.name}",
                settlement_ref=buy_debit_ref(str(purchase_id)),
            )
            s.add(debit)
            s.flush()
            debit_row_id = str(debit.id)

        pre_status = str(item.status) if is_selectable_status(item.status) else "wishlist"
        item.status = "bought"
        item.pre_buy_status = pre_status
        item.expense_id = str(exp.id)
        item.funding_source = src

        # B3: drain-and-last-link => archive the goal in the same transaction.
        goal_auto_archived = False
        if src == FUNDING_SAVINGS_GOAL and goal_name:
            goal_auto_archived = _autoarchive_goal_if_drained(
                s, user_id, goal_name, str(purchase_id))

        s.flush()
        log_audit(s, user_id, "BUY", "big_purchases", str(purchase_id),
                  {"expense_id": str(exp.id), "amount_eur": amount,
                   "funding_source": src, "goal": goal_name,
                   "debit_row": debit_row_id, "pre_buy_status": pre_status,
                   "goal_auto_archived": goal_auto_archived})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=(str(purchase_id), str(exp.id)))


def refund_wishlist_item(user_id: int, purchase_id: str) -> CommandResult:
    """Reverse a purchase exactly — ONE compensating transaction.

    Soft-deletes the linked expense and the goal debit row (both preserved
    for audit history) and restores the exact pre-buy status. A second
    refund attempt raises CommandError (nothing left to reverse). Legacy
    items bought without links restore status only."""
    from datetime import datetime, timezone
    from db import BIG_STATUSES, BigPurchase, Expense, Savings, log_audit
    s = _session()
    try:
        item = s.query(BigPurchase).filter(
            BigPurchase.id == str(purchase_id),
            BigPurchase.user_id == user_id).first()
        if item is None:
            raise CommandError("Wishlist item not found.")
        if str(item.status) != "bought":
            raise CommandError("Only bought items can be refunded.")
        now = datetime.now(timezone.utc)
        touched: list[str] = []

        if item.expense_id:
            exp = s.query(Expense).filter(
                Expense.id == str(item.expense_id),
                Expense.user_id == user_id,
                Expense.is_deleted.isnot(True)).first()
            if exp is not None:
                exp.is_deleted = True
                exp.deleted_at = now
                touched.append(str(exp.id))

        debit = s.query(Savings).filter(
            Savings.user_id == user_id,
            Savings.settlement_ref == buy_debit_ref(str(purchase_id)),
            Savings.is_deleted.isnot(True)).first()
        if debit is not None:
            debit.is_deleted = True
            debit.deleted_at = now
            touched.append(str(debit.id))

        restore = str(item.pre_buy_status or "wishlist")
        if restore not in BIG_STATUSES or restore == "bought":
            restore = "wishlist"
        item.status = restore
        item.pre_buy_status = None

        # B3: if this buy's drain auto-archived the funding goal, undo that
        # so the restored link resolves again. The archive also soft-deleted
        # this purchase's OWN debit row, so look it up regardless of state
        # (the filtered `debit` above can legitimately be None here).
        goal_auto_restored = False
        debit_any = s.query(Savings).filter(
            Savings.user_id == user_id,
            Savings.settlement_ref == buy_debit_ref(str(purchase_id))).first()
        if debit_any is not None and getattr(debit_any, "goal_name", None):
            goal_auto_restored = _autorestore_goal_if_archived(
                s, user_id, str(debit_any.goal_name), str(purchase_id))

        log_audit(s, user_id, "REFUND", "big_purchases", str(purchase_id),
                  {"restored_status": restore, "reversed": touched,
                   "goal_auto_restored": goal_auto_restored})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return CommandResult(changed=True, revision=_bump(user_id),
                         affected_ids=(str(purchase_id),))
