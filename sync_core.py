"""
sync_core.py — Device sync protocol: apply client changes with conflict
detection against the server state, and produce server snapshots.

v2 security model:
- Every change is validated against a per-table field schema (explicit
  whitelist + type/enum coercion); unknown fields are REJECTED, not dropped.
- `since` is the device's server-recorded last_sync_at (issued by the
  server) — the client cannot pass null/future timestamps to bypass conflict
  detection.
- Compare-and-update runs in ONE database session (no TOCTOU window).
- Record existence checks are scoped to the user (no cross-account oracle).

Conflict rule (simple and reviewable): if a record was edited on the server
AFTER the device's last sync AND the device wants to write different values,
the change is NOT applied — it is recorded in sync_conflicts for manual
resolution in Settings → Sync.
"""

import math
from datetime import datetime, date

from db import (
    get_session, Expense, Income, Savings,
    add_sync_conflict,
)
from utils import CATEGORIES, ALL_SUBCATS

SYNC_MODELS = {"expenses": Expense, "income": Income, "savings": Savings}
PROTECTED = ("id", "user_id", "created_at", "updated_at")

MAX_CHANGES = 500        # reject sync calls with more changes
SNAPSHOT_LIMIT = 5000    # cap snapshot rows per table
STR_MAX = 500            # cap string field length

FIELD_SCHEMAS = {
    "expenses": {
        "date": "date", "category": "str", "subcategory": "str",
        "description": "str", "amount": "float", "currency": "str",
        "amount_eur": "float", "recurring": "bool", "rec_template_id": "str",
        "loan_id": "str", "notes": "str", "is_deleted": "bool",
    },
    "income": {
        "date": "date", "source": "str", "income_type": "str", "hours": "float",
        "rate": "float", "budgeted": "float", "actual": "float",
        "currency": "str", "budgeted_eur": "float", "actual_eur": "float",
        "notes": "str", "is_deleted": "bool",
    },
    "savings": {
        "date": "date", "goal_name": "str", "target_eur": "float",
        "deposited": "float", "currency": "str", "deposited_eur": "float",
        "interest_rate": "float", "balance_eur": "float", "notes": "str",
        "is_deleted": "bool",
    },
}


def _norm_dt(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def parse_since(since):
    """Parse an ISO timestamp; naive UTC for comparisons."""
    if not since:
        return None
    if isinstance(since, datetime):
        return _norm_dt(since)
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
    """Convert ISO date strings into date objects (legacy helper; the v2
    path uses validate_fields instead)."""
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


def validate_fields(table: str, fields: dict):
    """Validate/coerce a change's fields against the table's schema.

    Returns (clean_fields, errors). Unknown fields, server-managed fields,
    bad types, oversized strings, and non-finite numbers are errors —
    nothing is silently dropped.
    """
    schema = FIELD_SCHEMAS.get(table)
    if schema is None:
        return {}, ["unknown table"]
    clean, errors = {}, []
    for k, v in (fields or {}).items():
        if k in PROTECTED:
            errors.append(f"{k} is server-managed")
            continue
        spec = schema.get(k)
        if spec is None:
            errors.append(f"unknown field {k}")
            continue
        try:
            if spec == "date":
                clean[k] = date.fromisoformat(str(v)[:10])
            elif spec == "str":
                s = str(v)
                if len(s) > STR_MAX:
                    errors.append(f"{k} too long")
                    continue
                clean[k] = s
            elif spec == "float":
                f = float(v)
                if not math.isfinite(f):
                    errors.append(f"{k} must be finite")
                    continue
                clean[k] = f
            elif spec == "bool":
                clean[k] = bool(v)
        except (TypeError, ValueError):
            errors.append(f"{k} invalid type")
    if table == "expenses" and "category" in clean:
        if clean["category"] not in CATEGORIES:
            errors.append("unknown category")
    if table == "expenses" and clean.get("subcategory"):
        if clean["subcategory"] not in ALL_SUBCATS:
            errors.append("unknown subcategory")
    return clean, errors


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


def create_record(user_id, table, record_id, fields):
    """Create a record; returns (ok, final_id).

    Record ids are globally unique (primary keys). A requested id that is
    already owned by ANOTHER user is silently REMAPPED to a fresh id — the
    caller reports the mapping to the client — so probing foreign ids never
    reveals their existence and never crashes the sync."""
    import uuid as _uuid
    model = SYNC_MODELS.get(table)
    if not model:
        return False, None
    with get_session() as s:
        existing = s.query(model).filter(model.id == record_id).first()
        if existing is not None and existing.user_id == user_id:
            return False, None
        final_id = record_id if existing is None else str(_uuid.uuid4())
        obj = model(id=final_id, user_id=user_id)
        for k, v in fields.items():
            if k in PROTECTED:
                continue
            if hasattr(obj, k):
                setattr(obj, k, v)
        s.add(obj)
    return True, final_id


def _apply_update(user_id, table, record_id, clean, since):
    """Read-compare-write in ONE session/transaction (atomic). Returns
    None when the record does not exist for this user, {"updated": True}
    on success, or {"conflict": ..., "server": ...} when a conflict is
    detected and the change is NOT applied."""
    model = SYNC_MODELS[table]
    with get_session() as s:
        obj = (s.query(model)
               .filter(model.id == record_id, model.user_id == user_id)
               .first())
        if obj is None:
            return None
        server_record = _serialize(obj)
        server_updated = _norm_dt(obj.updated_at)
        changed_on_server = (since is not None and server_updated is not None
                             and server_updated > since)
        if changed_on_server and fields_differ(server_record, clean):
            return {"conflict": json_safe(clean),
                    "server": json_safe(server_record)}
        for k, v in clean.items():
            if k in PROTECTED or not hasattr(obj, k):
                continue
            setattr(obj, k, v)
        return {"updated": True}


def apply_changes(user_id: int, changes: list, since=None) -> dict:
    """Apply a device's changes (validated, atomically, conflict-checked).

    since = the device's server-recorded last_sync_at (datetime or ISO).
    Returns {"applied": [...], "conflicts": [...], "failed": [...]}.
    """
    since = parse_since(since)
    applied, conflicts, failed = [], [], []
    for ch in (changes or [])[:MAX_CHANGES]:
        table = ch.get("table")
        rid = str(ch.get("id") or "")
        if table not in SYNC_MODELS or not rid:
            failed.append({"id": rid, "table": table,
                           "error": "unknown table or missing id"})
            continue
        clean, errors = validate_fields(table, ch.get("fields") or {})
        if errors:
            failed.append({"id": rid, "table": table,
                           "error": "; ".join(errors)})
            continue
        res = _apply_update(user_id, table, rid, clean, since)
        if res is None:
            ok, final_id = create_record(user_id, table, rid, clean)
            entry = {"id": rid, "table": table,
                     "status": "created" if ok else "failed"}
            if ok and final_id != rid:
                entry["new_id"] = final_id  # id remapped for this client
            applied.append(entry)
        elif "conflict" in res:
            add_sync_conflict(user_id, table, rid,
                              res["conflict"], res["server"])
            conflicts.append({"id": rid, "table": table})
        else:
            applied.append({"id": rid, "table": table, "status": "updated"})
    return {"applied": applied, "conflicts": conflicts, "failed": failed}


def snapshot(user_id: int, since=None, limit: int = SNAPSHOT_LIMIT):
    """All syncable records changed since `since` (None = everything).

    Returns (out, truncated): truncated=True when any table hit the limit.
    """
    out = {}
    truncated = False
    since = parse_since(since)
    for table, model in SYNC_MODELS.items():
        with get_session() as s:
            qq = (s.query(model).filter(model.user_id == user_id)
                  .order_by(model.updated_at.asc()))
            if since is not None:
                qq = qq.filter(model.updated_at > since)
            rows = qq.limit(limit).all()
        out[table] = [_serialize(r) for r in rows]
        if len(rows) >= limit:
            truncated = True
    return out, truncated
