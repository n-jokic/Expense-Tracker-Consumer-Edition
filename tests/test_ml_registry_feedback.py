from datetime import datetime, timezone, date

import pytest

from db import (
    init_db, create_user, delete_user_account, add_expense, update_expense,
    list_ml_models, save_ml_model, activate_ml_model,
    record_ml_feedback, get_ml_feedback, username_exists,
    get_user_by_username,
)
from auth import hash_password
from ml.registry import ModelInfo


@pytest.fixture()
def test_user():
    init_db()
    username = "ml_registry_test_user"
    email = "ml_registry_test@example.com"
    if username_exists(username):
        delete_user_account(get_user_by_username(username)["id"])
    uid = create_user(username, email, hash_password("test1234"), "ML Registry Tester")
    yield uid
    delete_user_account(uid)


def test_model_registry_is_per_user_and_requires_explicit_activation(test_user):
    info = ModelInfo("categorizer", 0, 3, datetime.now(timezone.utc), "abc", {"f1": .9})
    saved = save_ml_model(test_user, info)
    assert saved.version == 1
    assert list_ml_models(test_user)[0].version == 1
    assert activate_ml_model(test_user, "categorizer", 1).version == 1


def test_ml_feedback_is_append_only_and_canonicalized(test_user):
    eid = add_expense(test_user, {
        "date": date.today(), "category": "Dining Out", "description": "MCDONALD'S 553",
        "amount": 5, "amount_eur": 5, "suggest_source": "classifier",
        "suggest_confidence": .98, "suggest_model_version": 1,
        "suggest_category": "Groceries",
    })
    rows = get_ml_feedback(test_user)
    assert rows and rows[0]["merchant_canonical"] == "mcdonalds"
    assert rows[0]["selected_category"] == "Dining Out"
    update_expense(test_user, eid, {"category": "Groceries"})
    assert len(get_ml_feedback(test_user)) == 2


def test_record_ml_feedback_rejects_mutation_payload(test_user):
    record_ml_feedback(test_user, {"raw_description": "Lidl", "predicted_category": "Groceries"})
    assert get_ml_feedback(test_user)[0]["raw_description"] == "Lidl"


def test_account_deletion_removes_ml_records(test_user):
    info = ModelInfo("categorizer", 0, 3, datetime.now(timezone.utc), "abc", {"f1": .9})
    save_ml_model(test_user, info)
    record_ml_feedback(test_user, {"raw_description": "Lidl", "predicted_category": "Groceries"})

    assert delete_user_account(test_user) is True
    assert list_ml_models(test_user) == []
    assert get_ml_feedback(test_user) == []
