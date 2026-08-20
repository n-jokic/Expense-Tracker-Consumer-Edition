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


def bulk_update_expenses(user_id: int, updates: list[dict]) -> CommandResult:
    """Each dict: {"id": str, "fields": dict}. One transaction, one revision bump."""
    if not updates:
        return CommandResult(changed=False, revision=None, affected_ids=())
    from db import get_engine, Expense, log_audit
    from sqlalchemy.orm import sessionmaker
    engine = get_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    affected: list[str] = []
    try:
        for item in updates:
            eid = str(item.get("id") or "")
            fields = dict(item.get("fields") or {})
            if not eid or not fields:
                continue
            obj = s.query(Expense).filter(Expense.id == eid, Expense.user_id == user_id).first()
            if obj is None:
                continue
            for k, v in fields.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            log_audit(s, user_id, "UPDATE", "expenses", eid, fields)
            affected.append(eid)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    rev = _bump(user_id) if affected else None
    return CommandResult(changed=bool(affected), revision=rev, affected_ids=tuple(affected))


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
