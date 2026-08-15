"""
sync_core.py — Device sync protocol: apply client changes with conflict
detection against the server state, and produce server snapshots.

Conflict rule (simple and reviewable): if a record was edited on the server
AFTER the device's last sync (`updated_at > since`) AND the device wants to
write different values, the change is NOT applied — it is recorded in
sync_conflicts for manual resolution in Settings → Sync.
"""

from datetime import datetime, date

from db import (
    get_session, Expense, Income, Savings,
    add_sync_conflict, apply_record_fields,
)

SYNC_MODELS = {"expenses": Expense, "income": Income, "savings": Savings}
PROTECTED = ("id", "user_id", "created_at", "updated_at")


def _norm_dt(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def parse_since(since: str | None):
    """Parse an ISO timestamp from a device; naive UTC for comparisons."""
    if not since:
        return None
    try:
        return _norm_dt(datetime.fromisoformat(str(since).replace("Z", "+00:00")))
    except Exception:
        return None


def _serialize(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[c.name] = v
    return out


def coerce_fields(fields: dict) -> dict:
    """Convert ISO date strings into date objects; drop junk values."""
    out = {}
    for k, v in (fields or {}).items():
        if k == "date" and isinstance(v, str):
            try:
                v = date.fromisoformat(v[:10])
            except ValueError:
                continue
        out[k] = v
    return out


def json_safe(fields: dict) -> dict:
    """JSON-serializable copy of fields (dates/datetimes -> ISO strings)."""
    out = {}
    for k, v in (fields or {}).items():
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[k] = v
    return out


def _server_row(user_id, table, record_id):
    model = SYNC_MODELS.get(table)
    if not model:
        return None
    with get_session() as s:
        obj = (s.query(model)
               .filter(model.id == record_id, model.user_id == user_id).first())
        if not obj:
            return None
        return {"updated_at": _norm_dt(obj.updated_at), "record": _serialize(obj)}


def fields_differ(server_record: dict, fields: dict) -> bool:
    """True when the device's field values differ from the server's."""
    for k, v in (fields or {}).items():
        if k in PROTECTED or k not in server_record:
            continue
        sv = server_record[k]
        try:
            if float(sv) == float(v):
                continue
        except (TypeError, ValueError):
            pass
        if str(sv) != str(v):
            return True
    return False


def create_record(user_id, table, record_id, fields) -> bool:
    model = SYNC_MODELS.get(table)
    if not model:
        return False
    with get_session() as s:
        if s.query(model).filter(model.id == record_id).first():
            return False
        obj = model(id=record_id, user_id=user_id)
        for k, v in fields.items():
            if k in PROTECTED:
                continue
            if hasattr(obj, k):
                setattr(obj, k, v)
        s.add(obj)
    return True


def apply_changes(user_id: int, changes: list, since) -> dict:
    """Apply a device's changes. Returns {"applied": [...], "conflicts": [...]}."""
    applied = []
    conflicts = []
    for ch in changes:
        table = ch.get("table")
        rid = ch.get("id")
        fields = ch.get("fields") or {}
        if table not in SYNC_MODELS or not rid:
            continue

        server = _server_row(user_id, table, rid)
        if server is None:
            ok = create_record(user_id, table, rid, coerce_fields(fields))
            applied.append({"id": rid, "table": table,
                            "status": "created" if ok else "failed"})
            continue

        changed_on_server = (since is not None
                             and server["updated_at"] is not None
                             and server["updated_at"] > since)
        if changed_on_server and fields_differ(server["record"], fields):
            # store JSON-safe values: coerce_fields may contain date objects
            add_sync_conflict(user_id, table, rid,
                              json_safe(coerce_fields(fields)),
                              json_safe(server["record"]))
            conflicts.append({"id": rid, "table": table})
        else:
            apply_record_fields(user_id, table, rid, coerce_fields(fields))
            applied.append({"id": rid, "table": table, "status": "updated"})
    return {"applied": applied, "conflicts": conflicts}


def snapshot(user_id: int, since=None) -> dict:
    """All syncable records changed since `since` (None = everything)."""
    out = {}
    since = parse_since(since) if isinstance(since, str) else since
    for table, model in SYNC_MODELS.items():
        with get_session() as s:
            qq = s.query(model).filter(model.user_id == user_id)
            if since is not None:
                qq = qq.filter(model.updated_at > since)
            rows = qq.all()
        out[table] = [_serialize(r) for r in rows]
    return out
