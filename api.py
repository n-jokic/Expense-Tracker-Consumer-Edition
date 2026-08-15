"""
api.py — Phone sync API (FastAPI). Run separately:  python api.py   (port 8502)

Used by the (future) offline phone PWA: pair with a one-time code, then push
local changes and pull a server snapshot. Conflicts are recorded and resolved
in the web app (Settings → Sync).
"""

from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

from db import init_db, complete_pairing, device_by_token, touch_device_sync
import sync_core

app = FastAPI(title="Expense Tracker Sync API")


class PairRequest(BaseModel):
    code: str
    device_name: str = "Phone"


class Change(BaseModel):
    table: str
    id: str
    fields: dict


class SyncRequest(BaseModel):
    since: Optional[str] = None
    changes: list[Change] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/pair")
def pair(req: PairRequest):
    init_db()
    token = complete_pairing(req.code, req.device_name)
    if not token:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    dev = device_by_token(token)
    return {"token": token, "user_id": dev["user_id"]}


@app.post("/api/sync")
def sync(req: SyncRequest, authorization: Optional[str] = Header(default=None)):
    init_db()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.removeprefix("Bearer ").strip()
    dev = device_by_token(token)
    if not dev:
        raise HTTPException(status_code=401, detail="Invalid token")

    since = sync_core.parse_since(req.since)
    result = sync_core.apply_changes(dev["user_id"],
                                     [c.model_dump() for c in req.changes], since)
    touch_device_sync(dev["id"])
    snap = sync_core.snapshot(dev["user_id"], since)
    return {"applied": result["applied"], "conflicts": result["conflicts"],
            "snapshot": snap}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8502)
