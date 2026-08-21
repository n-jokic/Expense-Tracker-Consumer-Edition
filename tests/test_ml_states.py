"""
ML-01 regression tests — explicit empty/candidate/active states, audited
and non-destructive transitions.

State machine under test (db-level):
  save -> candidate -> activate -> active
  deactivate -> keeps artifacts + history, idempotent
  discard(candidate) -> removes one version only, active untouched
  retrain while active -> registers a NEW version, never mutates the active row
"""

import pytest

import db
from auth import hash_password
from ml.registry import make_model_info

U = "ml01_user"
E = "ml01@example.com"
NAME = "expense_categorizer"


def _info(version_hint=0, fp="fp-A", rows=42, acc=0.87):
    return make_model_info(NAME, version_hint, rows, fp,
                           {"accuracy": acc, "auto_threshold": 0.62})


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "ML01 Tester")
    yield uid
    db.delete_user_account(uid)


def _versions(uid):
    return [m.version for m in db.list_ml_models(uid, NAME)]


# ── Core state machine ───────────────────────────────────────────────────────

def test_save_then_activate_makes_version_active(user):
    info = db.save_ml_model(user, _info())
    assert _versions(user) == [info.version]
    assert db.get_active_ml_model(user, NAME) is None      # candidate only

    db.activate_ml_model(user, NAME, info.version)
    active = db.get_active_ml_model(user, NAME)
    assert active is not None and active.version == info.version


def test_retrain_while_active_creates_new_candidate_not_mutating_active(user):
    v1 = db.save_ml_model(user, _info(fp="fp-1"))
    db.activate_ml_model(user, NAME, v1.version)
    before = db.get_active_ml_model(user, NAME)

    v2 = db.save_ml_model(user, _info(fp="fp-2", acc=0.91))
    assert v2.version == v1.version + 1                    # per-user versions
    after = db.get_active_ml_model(user, NAME)
    assert after.version == before.version                 # active untouched
    assert sorted(_versions(user)) == [v1.version, v2.version]


# ── Deactivate: non-destructive ──────────────────────────────────────────────

def test_deactivate_keeps_artifacts_and_history_and_is_idempotent(user):
    v1 = db.save_ml_model(user, _info(fp="f1"))
    v2 = db.save_ml_model(user, _info(fp="f2"))
    db.activate_ml_model(user, NAME, v1.version)

    deactivated = db.deactivate_ml_model(user, NAME)
    assert deactivated is not None and deactivated.version == v1.version
    assert db.get_active_ml_model(user, NAME) is None
    assert sorted(_versions(user)) == [v1.version, v2.version]   # history kept

    # idempotent: deactivating with nothing active is a clean no-op
    assert db.deactivate_ml_model(user, NAME) is None
    assert sorted(_versions(user)) == [v1.version, v2.version]

    # reactivation works afterwards
    db.activate_ml_model(user, NAME, v2.version)
    assert db.get_active_ml_model(user, NAME).version == v2.version


# ── Discard: candidate-only, never the active model ─────────────────────────

def test_discard_removes_one_candidate_only(user):
    v1 = db.save_ml_model(user, _info(fp="f1"))
    v2 = db.save_ml_model(user, _info(fp="f2"))
    db.activate_ml_model(user, NAME, v1.version)

    assert db.discard_ml_model_version(user, NAME, v2.version) is True
    assert _versions(user) == [v1.version]
    assert db.get_active_ml_model(user, NAME).version == v1.version


def test_discard_active_version_is_rejected(user):
    v1 = db.save_ml_model(user, _info())
    db.activate_ml_model(user, NAME, v1.version)
    with pytest.raises(ValueError):
        db.discard_ml_model_version(user, NAME, v1.version)
    assert db.get_active_ml_model(user, NAME).version == v1.version


def test_discard_unknown_version_is_false(user):
    assert db.discard_ml_model_version(user, NAME, 999) is False


def test_discard_without_active_leaves_candidates_manageable(user):
    v1 = db.save_ml_model(user, _info())
    v2 = db.save_ml_model(user, _info(fp="f2"))
    assert db.discard_ml_model_version(user, NAME, v1.version) is True
    assert _versions(user) == [v2.version]


# ── Audited transitions ──────────────────────────────────────────────────────

def test_transitions_are_audited(user):
    import json as _json
    v1 = db.save_ml_model(user, _info())
    db.activate_ml_model(user, NAME, v1.version)
    db.deactivate_ml_model(user, NAME)
    db.discard_ml_model_version(user, NAME, v1.version)

    log = db.get_audit_log(user)
    actions = set(log["action"])
    assert {"UPDATE", "DELETE"} <= actions
    upd = log[(log["action"] == "UPDATE")
              & (log["table_name"] == "ml_models")]
    assert len(upd) >= 1
    details = _json.loads(upd.iloc[0]["details"] or "{}")
    assert details["action"] == "deactivate"


# ── UI contract: the three states are rendered explicitly ────────────────────

def test_settings_page_renders_all_three_states_in_source():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath(
        "app_pages", "settings.py").read_text(encoding="utf-8")
    # EMPTY state explains what happens next and the keyword fallback…
    assert "No trained model yet" in src
    assert "keyword rules" in src
    # …ACTIVE state shows status, metrics and a non-destructive deactivate…
    assert ":green-badge[Active]" in src
    assert "Deactivate" in src
    assert "nothing was deleted" in src
    # …CANDIDATE state awaits explicit review with activate/discard…
    assert "Candidate" in src and "awaiting your review" in src
    assert '"Activate"' in src and '"Discard"' in src
    # …metrics are formatted (percent formatting helper present)…
    assert "_fmt_metrics" in src
    assert ":.0%" in src
    # …and automatic training is described accurately.
    assert "never goes live on its" in src
