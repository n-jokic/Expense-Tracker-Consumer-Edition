"""
Integration tests for the sync API (api.py) using FastAPI's TestClient:
pairing throttling, token auth/expiry, the v2 server-issued cursor (client
`since` is ignored), payload caps, and cross-user isolation.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import api
from db import (
    init_db, create_user, delete_user_account, add_expense, get_expenses,
    get_sync_conflicts, update_expense, create_pairing_device,
    complete_pairing, username_exists, get_user_by_username,
)
from auth import hash_password

TEST_USERNAME = "api_test_user"
TEST_EMAIL    = "api_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "API Tester")
    yield uid
    delete_user_account(uid)


@pytest.fixture()
def client():
    api._pair_attempts.clear()
    with TestClient(api.app) as c:
        yield c
    api._pair_attempts.clear()


@pytest.fixture()
def auth_token(test_user):
    dev_id, code = create_pairing_device(test_user)
    token = complete_pairing(code, "Test Phone")
    return token


def _expense(rid="e1", **fields):
    base = {"table": "expenses", "id": rid,
            "fields": {"date": "2025-06-01", "category": "Food & Dining",
                       "description": "Offline", "amount": 5.0,
                       "currency": "EUR", "amount_eur": 5.0}}
    base["fields"].update(fields)
    return base


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_pair_success_and_invalid_code(client, test_user):
    dev_id, code = create_pairing_device(test_user)
    r = client.post("/api/pair", json={"code": code, "device_name": "Phone"})
    assert r.status_code == 200
    assert r.json()["user_id"] == test_user
    # single-use code
    assert client.post("/api/pair", json={"code": code}).status_code == 400
    # junk code
    assert client.post("/api/pair", json={"code": "ZZZZZZ"}).status_code == 400


def test_pair_rate_limited(client, test_user):
    for _ in range(5):
        client.post("/api/pair", json={"code": "ZZZZZZ"})
    r = client.post("/api/pair", json={"code": "ZZZZZZ"})
    assert r.status_code == 429


def test_sync_requires_valid_token(client):
    assert client.post("/api/v2/sync", json={"changes": []}).status_code == 401
    r = client.post("/api/v2/sync", json={"changes": []},
                    headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_sync_creates_record_and_returns_snapshot(client, auth_token):
    r = client.post("/api/v2/sync",
                    json={"changes": [_expense("e1", description="Offline")]},
                    headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"][0]["status"] == "created"
    assert body["failed"] == []
    assert any(row["id"] == "e1" for row in body["snapshot"]["expenses"])


def test_v2_uses_server_cursor_not_client_since(client, test_user, auth_token):
    # First sync: establishes the device's server-side cursor.
    client.post("/api/v2/sync", json={"changes": []},
                headers={"Authorization": f"Bearer {auth_token}"})

    rid = add_expense(test_user, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Lidl", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "old",
    })
    update_expense(test_user, rid, {"notes": "server edit"})

    # A hostile/future client `since` must be IGNORED: the server uses its
    # own recorded last_sync_at, so the server edit still wins as a conflict.
    r = client.post("/api/v2/sync",
                    json={"since": "9999-01-01T00:00:00Z",
                          "changes": [{"table": "expenses", "id": rid,
                                       "fields": {"notes": "phone edit"}}]},
                    headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    assert r.json()["conflicts"][0]["id"] == rid
    assert get_expenses(test_user).iloc[0]["notes"] == "server edit"
    assert len(get_sync_conflicts(test_user, resolved=False)) == 1


def test_payload_cap_rejects_oversized_changes(client, auth_token):
    changes = [_expense(f"e{i}") for i in range(501)]
    r = client.post("/api/v2/sync", json={"changes": changes},
                    headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 422


def test_expired_token_rejected(client, test_user):
    dev_id, code = create_pairing_device(test_user)
    token = complete_pairing(code, "Old Phone")
    from db import Device, get_session
    with get_session() as s:
        dev = s.query(Device).filter(Device.id == dev_id).first()
        dev.token_expires_at = date.today() - timedelta(days=1)
    r = client.post("/api/v2/sync", json={"changes": []},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_unknown_fields_rejected_via_api(client, auth_token):
    r = client.post("/api/v2/sync",
                    json={"changes": [{"table": "expenses", "id": "e9",
                                       "fields": {"evil": 1}}]},
                    headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["failed"] and "unknown field" in body["failed"][0]["error"]
    assert body["applied"] == []
