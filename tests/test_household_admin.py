"""#21b — household admin kick + configurable sharing."""
import pytest

from auth import hash_password
from db import (DEFAULT_SHARE_PREFS, SHARE_AREAS, SHARE_LEVELS,
                create_household, create_user, delete_user_account,
                get_household_by_member, get_household_members,
                get_share_prefs, get_settings, get_user_by_username,
                init_db, is_household_owner, join_household,
                kick_household_member, leave_household, save_share_prefs,
                username_exists)

USERS = []


def _mkuser(n):
    uname = f"hh{n}_{datetime_suffix}"
    uid = create_user(uname, f"{uname}@x.io", hash_password("test1234"),
                      f"Member {n}")
    USERS.append(uid)
    return uid


def setup_module(module):
    global datetime_suffix
    import time
    datetime_suffix = str(int(time.time() * 1000) % 10_000_000)
    init_db()
    _purge_leaked()


def teardown_module(module):
    # USERS stores real ids from create_user — delete them directly. A
    # leaked member shifts the global lowest-user-id order that MCP's
    # default _resolve_user() relies on, so this cleanup MUST run.
    for uid in USERS:
        try:
            delete_user_account(int(uid))
        except Exception:
            pass


def _purge_leaked(prefix="hh"):
    """Remove leftover dynamic users from an aborted previous run."""
    from db import get_session, User
    with get_session() as s:
        rows = s.query(User).filter(User.username.like(f"{prefix}%")).all()
        ids = [u.id for u in rows if u.username.startswith(prefix)
               and u.email.endswith("@x.io")]
    for i in ids:
        try:
            delete_user_account(int(i))
        except Exception:
            pass


def test_kick_owner_only_and_revision_bump():
    owner = _mkuser(1)
    member = _mkuser(2)
    hh_id, code = create_household(owner, "Test HH")
    join_household(member, code)
    assert is_household_owner(owner) is True
    assert is_household_owner(member) is False

    rev_member_before = get_user_row(member)["data_revision"]
    name = kick_household_member(owner, member)
    assert name
    assert get_user_row(member)["household_id"] is None
    assert get_user_row(member)["data_revision"] > rev_member_before
    # removed member's revision bumped even though they have no household now
    assert is_household_owner(member) is False

    with pytest.raises(ValueError):
        kick_household_member(owner, owner)          # owner cannot kick self
    # non-owner (the ex-member rejoining then trying) cannot kick
    join_household(member, code)
    before = get_user_row(owner)["household_id"]
    with pytest.raises(PermissionError):
        kick_household_member(member, owner)
    assert get_user_row(owner)["household_id"] == before


def test_share_prefs_gating_and_permissions():
    owner = _mkuser(3)
    member = _mkuser(4)
    hh_id, code = create_household(owner, "Share HH")
    join_household(member, code)

    prefs = get_share_prefs(hh_id)
    assert prefs["expenses"] == "editable"           # always shared
    assert prefs["budgets"] == "hidden"              # defaults hidden
    assert set(prefs) == set(SHARE_AREAS)

    save_share_prefs(hh_id, owner, {"budgets": "visible"})
    assert get_share_prefs(hh_id)["budgets"] == "visible"

    with pytest.raises(PermissionError):
        save_share_prefs(hh_id, member, {"income": "editable"})
    with pytest.raises(ValueError):
        save_share_prefs(hh_id, owner, {"budgets": "shiny"})
    # invalid level persisted nothing
    assert get_share_prefs(hh_id)["income"] == "hidden"


def test_prefs_survive_member_leave_and_rejoin():
    owner = _mkuser(5)
    member = _mkuser(6)
    hh_id, code = create_household(owner, "Persist HH")
    join_household(member, code)
    save_share_prefs(hh_id, owner, {"loans": "editable"})
    leave_household(member)
    assert get_share_prefs(hh_id)["loans"] == "editable"
    join_household(member, code)
    assert get_share_prefs(hh_id)["loans"] == "editable"
    # owner leaving transfers ownership; prefs still intact
    other = _mkuser(7)
    join_household(other, code)
    leave_household(owner)
    hh2 = get_household_by_member(member)
    assert hh2 and hh2["id"] == hh_id
    assert is_household_owner(member, hh_id) is True
    assert get_share_prefs(hh_id)["budgets"] == DEFAULT_SHARE_PREFS["budgets"]


def get_user_row(uid):
    from db import get_session, User
    with get_session() as s:
        u = s.query(User).filter(User.id == int(uid)).first()
        return {"id": u.id, "household_id": u.household_id,
                "data_revision": int(u.data_revision or 0)}
