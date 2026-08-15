"""
api.py — Phone sync API (FastAPI). Run separately:  python api.py   (port 8502)

Used by the (future) offline phone PWA: pair with a one-time code, then push
local changes and pull a server snapshot. Conflicts are recorded and resolved
in the web app (Settings → Sync).

v2 (/api/v2/sync) is the secure protocol:
- the sync cursor is the device's server-recorded last_sync_at — the client
  cannot pass null/future timestamps to bypass conflict detection;
- every change is validated against per-table field schemas (sync_core);
- compare-and-update is atomic; payload sizes are capped.
v1 (/api/sync) remains for compatibility and is deprecated.

EXPERIMENTAL: this API and the phone pairing flow are under active
development — see README for the caveats.
"""

import os
import time
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel, Field

from db import init_db, complete_pairing, device_by_token, touch_device_sync
import sync_core

app = FastAPI(title="Expense Tracker Sync API")


# ── Pairing rate limiting (in-memory, per client IP) ─────────────────────────
_PAIR_WINDOW_S = 600          # 10 minutes
_PAIR_MAX_ATTEMPTS = 5
_pair_lock = threading.Lock()
_pair_attempts: dict = {}


def _pair_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _pair_lock:
        attempts = [t for t in _pair_attempts.get(ip, [])
                    if now - t < _PAIR_WINDOW_S]
        _pair_attempts[ip] = attempts
        if len(attempts) >= _PAIR_MAX_ATTEMPTS:
            return True
        attempts.append(now)
        return False


# ── Request models ────────────────────────────────────────────────────────────

class PairRequest(BaseModel):
    code: str = Field(max_length=20)
    device_name: str = "Phone"


class Change(BaseModel):
    table: str
    id: str
    fields: dict


class SyncRequest(BaseModel):
    since: Optional[str] = None
    changes: list[Change] = Field(default_factory=list,
                                  max_length=sync_core.MAX_CHANGES)


def _auth(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.removeprefix("Bearer ").strip()
    dev = device_by_token(token)
    if not dev:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return dev


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/pair")
def pair(req: PairRequest, request: Request):
    init_db()
    ip = request.client.host if request.client else "unknown"
    if _pair_rate_limited(ip):
        raise HTTPException(status_code=429,
                            detail="Too many pairing attempts — try again in 10 minutes")
    token = complete_pairing(req.code, req.device_name)
    if not token:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    dev = device_by_token(token)
    return {"token": token, "user_id": dev["user_id"]}


@app.post("/api/sync", deprecated=True)
def sync_v1(req: SyncRequest, authorization: Optional[str] = Header(default=None)):
    """DEPRECATED: client-supplied `since` is not trusted going forward.
    Use /api/v2/sync (server-issued cursor)."""
    dev = _auth(authorization)
    init_db()
    since = sync_core.parse_since(req.since)
    result = sync_core.apply_changes(dev["user_id"],
                                     [c.model_dump() for c in req.changes], since)
    touch_device_sync(dev["id"])
    snap, _truncated = sync_core.snapshot(dev["user_id"], since)
    return {"applied": result["applied"], "conflicts": result["conflicts"],
            "snapshot": snap}


@app.post("/api/v2/sync")
def sync_v2(req: SyncRequest, authorization: Optional[str] = Header(default=None)):
    dev = _auth(authorization)
    init_db()
    # Server-issued cursor: the device's recorded last sync time. A client
    # cannot forge a null/future `since` to skip conflict detection.
    since = sync_core.parse_since(dev.get("last_sync_at"))
    result = sync_core.apply_changes(dev["user_id"],
                                     [c.model_dump() for c in req.changes], since)
    touch_device_sync(dev["id"])
    snap, truncated = sync_core.snapshot(dev["user_id"], since)
    return {"applied": result["applied"], "conflicts": result["conflicts"],
            "failed": result["failed"], "snapshot": snap,
            "truncated": truncated}


if __name__ == "__main__":
    import uvicorn
    kwargs = {"host": "0.0.0.0", "port": 8502}
    if os.environ.get("EXPENSE_TRACKER_TLS") == "1":
        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "certs")
        cert = os.path.join(cert_dir, "cert.pem")
        key = os.path.join(cert_dir, "key.pem")
        if os.path.exists(cert) and os.path.exists(key):
            kwargs.update(ssl_certfile=cert, ssl_keyfile=key)
    uvicorn.run(app, **kwargs)
