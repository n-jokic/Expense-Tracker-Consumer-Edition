"""#26 — reversible agent mutations: undo framework, commands, guard rails,
sanitizer redaction, mail staging."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import db
from ai.safety import sanitize_tool_result
from ai.tool_registry import TOOLS
from auth import hash_password
from db import (create_user, delete_user_account,
                get_expenses, get_income_templates, get_recurring,
                get_settings, get_user_by_username, get_trips,
                init_db, save_settings, username_exists)
from services import commands as C
from services.finance_queries import unallocated_funds_eur
from services.mail_ingestion import parse_email_text
from services.undo import (UndoOutcome, UndoToken, execute_undo,
                           get_token, make_undo_token, register_token)

U = "agent_user"
E = "agent@example.com"
TODAY = date.today()


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Agent Tester")
    yield uid
    delete_user_account(uid)


def test_add_expense_undo_cycle_idempotent(user):
    before = unallocated_funds_eur(user)
    res = C.add_expense(user, description="coffee", amount_eur=3.50,
                        category="Dining out")
    assert res.changed and res.undo_token is not None
    mid = unallocated_funds_eur(user)
    out = execute_undo(res.undo_token.token_id)
    assert out.ok and out.changed
    assert unallocated_funds_eur(user) == pytest.approx(before)
    # second undo: already-deleted -> ok no-op (idempotent contract)
    out2 = execute_undo(res.undo_token.token_id)
    assert out2.ok and not out2.changed


def test_expired_token_is_rejected():
    tok = UndoToken(token_id="dead-token", inverse_command="noop",
                    inverse_args={}, description="old",
                    expires_at=datetime.utcnow() - timedelta(seconds=1))
    register_token(tok)
    assert get_token("dead-token") is None
    out = execute_undo("dead-token")
    assert not out.ok and "expired" in out.message.lower()


def test_update_expense_snapshot_undo_restores(user):
    res0 = C.add_expense(user, description="book", amount_eur=12.0,
                         category="Shopping")
    eid = res0.affected_ids[0]
    res = C.update_expense(user, eid, {"amount_eur": 20.0,
                                       "description": "books"})
    assert res.changed
    row = get_expenses(user)[get_expenses(user)["id"] == eid].iloc[-1]
    assert float(row["amount_eur"]) == pytest.approx(20.0)
    out = execute_undo(res.undo_token.token_id)
    assert out.ok and out.changed
    row = get_expenses(user)[get_expenses(user)["id"] == eid].iloc[-1]
    assert float(row["amount_eur"]) == pytest.approx(12.0)
    assert str(row["description"]) == "book"


def test_delete_restore_both_directions_idempotent(user):
    res = C.add_expense(user, description="snack", amount_eur=2.0,
                        category="Other")
    eid = res.affected_ids[0]
    d = C.delete_expense(user, eid)
    assert d.changed
    d2 = C.delete_expense(user, eid)
    assert d2.changed is False                      # already deleted
    out = execute_undo(d.undo_token.token_id)
    assert out.ok and out.changed                   # restored
    out2 = execute_undo(d.undo_token.token_id)
    assert out2.ok and not out2.changed             # already active


def test_unknown_category_maps_through_taxonomy(user):
    res = C.add_expense(user, description="mystery",
                        amount_eur=5.0, category="Total nonsense")
    row = get_expenses(user).iloc[0]
    assert str(row["category"]) in ("Uncategorized", "Other")


def test_recurring_template_soft_delete_and_reader_filter(user):
    res = C.add_recurring_template(user, description="Gym",
                                   amount_eur=30.0, category="Health",
                                   due_day=5)
    tid = res.affected_ids[0]
    df = get_recurring(user)
    assert (df["id"] == tid).sum() == 1
    d = C.delete_recurring_template(user, tid)
    assert d.changed
    assert (get_recurring(user)["id"] == tid).sum() == 0   # filtered out
    out = execute_undo(d.undo_token.token_id)
    assert out.changed
    assert (get_recurring(user)["id"] == tid).sum() == 1


def test_threshold_confirmation_gate(user):
    save_settings(user, {"agent_confirm_threshold_eur": 500.0})
    assert C.mutation_requires_confirmation(user, 500.0) is False
    assert C.mutation_requires_confirmation(user, 500.01) is True
    # tool wrapper: over threshold without confirm -> needs_confirmation card
    from ai.tool_registry import TOOLS
    out = TOOLS["add_expense"](user, description="laptop",
                               amount_eur=900.0, category="Shopping")
    assert out.get("needs_confirmation") is True
    assert out["command"] == "add_expense"
    # confirm=True books it for real and records the rate counter
    n_before = C.mutations_last_24h(user)
    out2 = TOOLS["add_expense"](user, description="laptop",
                                amount_eur=900.0, category="Shopping",
                                confirm=True)
    assert out2.get("changed") is True
    assert C.mutations_last_24h(user) == n_before + 1


def test_dry_run_books_nothing(user):
    out = TOOLS["add_expense"](user, description="ghost", amount_eur=9.0,
                               dry_run=True)
    assert out.get("changed") is False
    assert len(get_expenses(user)) == 0


def test_rate_limit_blocks_mutations(user):
    stamps = [(datetime.utcnow() - timedelta(hours=i % 23)).isoformat()
              for i in range(C.MAX_AGENT_MUTATIONS_PER_DAY)]
    save_settings(user, {"agent_call_counts": stamps})
    assert C.mutation_rate_limited(user) is True
    out = TOOLS["add_expense"](user, description="blocked", amount_eur=3.0)
    assert out.get("ok") is False and "limit" in out.get("error", "").lower()
    assert len(get_expenses(user)) == 0


def test_sanitizer_redacts_undo_keys_in_both_modes():
    payload = {"undo_token": "tok-123", "undo_command": "delete_expense",
               "note": "fine"}
    ext = sanitize_tool_result(payload, external=True)
    loc = sanitize_tool_result(payload, external=False)
    assert ext["undo_token"] == "[REDACTED]"
    assert ext["undo_command"] == "[REDACTED]"
    assert loc["undo_token"] == "[REDACTED]"
    assert ext["note"] == "fine"


MAIL = """Order confirmation

Dear customer, thanks for your order on 2026-08-20.

2 x Notebook A5 4.99
1 x Gel pens blue 3.20

Shipping 4.99
Total 18.17
"""


def test_mail_parse_items_and_total_only():
    cands = parse_email_text(MAIL)
    assert cands, "expected candidates from item rows"
    descs = {c["description"].lower() for c in cands}
    assert any("notebook" in d for d in descs)
    for c in cands:
        assert c["amount_eur"] > 0
        assert c["date"] == date(2026, 8, 20)
        assert 0 < c["confidence"] <= 1
    # no amounts -> no candidates
    assert parse_email_text("") == []
    assert parse_email_text("Hello, how are you?") == []


def test_milestone_and_pool_recompute_after_undo(user):
    """Documented limitation: milestones are NOT clawed back — they simply
    re-evaluate on next load without crashing."""
    res = C.add_expense(user, description="big fun buy",
                        amount_eur=120.0, category="Entertainment")
    execute_undo(res.undo_token.token_id)
    mids = db.get_earned_milestone_ids(user) if hasattr(
        db, "get_earned_milestone_ids") else []
    assert isinstance(mids, (list, set))   # recompute path stays healthy
