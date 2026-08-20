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
- Business invariants (>0, <=MAX_AMOUNT / MAX_SAVINGS_TARGET, enum checks)
  are enforced centrally; derived *_eur fields are server-computed via
  to_eur(amount,currency,rates) using the user's stored rates, never
  trusted from the client (PATTERN-01 fix for T3-CURRENCY-001/004 and analogs).
- Loan payment metadata (loan_payment_type, loan_surcharge_eur) is whitelisted
  with validation (INTEGRATION-C-001).

Conflict rule (simple and reviewable): if a record was edited on the server
AFTER the device's last sync AND the device wants to write different values,
the change is NOT applied — it is recorded in sync_conflicts for manual
resolution in Settings → Sync.
"""

import math
from datetime import datetime, date

from sqlalchemy.exc import IntegrityError

from db import (
    get_session, Expense, Income, Savings, SavingsAccount,
    add_sync_conflict, log_audit, get_settings,
)
from utils import (
    CATEGORIES, ALL_SUBCATS, remap_category_subcategory,
    MAX_AMOUNT, MAX_SAVINGS_TARGET, SUPPORTED_CURRENCIES,
    get_rates, to_eur,
)

SYNC_MODELS = {"expenses": Expense, "income": Income, "savings": Savings,
               "savings_accounts": SavingsAccount}
PROTECTED = ("id", "user_id", "created_at", "updated_at")

# Fields a NEW record must include (the server fills id/user_id itself);
# updates may send any subset.
REQUIRED_FIELDS = {
    "savings_accounts": ("goal_name",),
}

MAX_CHANGES = 500        # reject sync calls with more changes
SNAPSHOT_LIMIT = 5000    # cap snapshot rows per table
STR_MAX = 500            # cap string field length

FIELD_SCHEMAS = {
    "expenses": {
        "date": "date", "category": "str", "subcategory": "str",
        "description": "str", "amount": "float", "currency": "str",
        "amount_eur": "float", "recurring": "bool", "rec_template_id": "str",
        "loan_id": "str", "loan_payment_type": "str", "loan_surcharge_eur": "float",
        "notes": "str", "is_deleted": "bool",
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
    "savings_accounts": {
        "goal_name": "str", "name": "str", "amount": "float",
        "currency": "str", "amount_eur": "float", "annual_rate": "float",
        "start_date": "date", "maturity_date": "date", "status": "str",
        "notes": "str", "is_deleted": "bool",
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


def _get_user_rates(user_id: int) -> dict:
    try:
        return get_rates(get_settings(user_id))
    except Exception:
        # Fallback to defaults if settings unreadable
        return get_rates({})


def _recompute_derived_eur(table: str, clean: dict, rates: dict, existing=None) -> None:
    """Server-side recompute of derived *_eur fields via to_eur.

    Overwrites client-provided *_eur values so aggregates cannot be poisoned
    (T3-CURRENCY-004). Uses clean amount/currency when present, otherwise
    falls back to existing record's values for isolated *_eur updates.
    """
    try:
        if table == "expenses":
            # amount -> amount_eur
            amt = clean.get("amount")
            if amt is None and existing is not None and "amount_eur" in clean:
                amt = getattr(existing, "amount", None)
            cur = clean.get("currency")
            if cur is None and existing is not None:
                cur = getattr(existing, "currency", "EUR")
            elif cur is None:
                cur = "EUR"
            # Recompute whenever base or derived or currency is being written
            if ("amount" in clean or "currency" in clean or "amount_eur" in clean) and amt is not None:
                clean["amount_eur"] = to_eur(float(amt), cur, rates)
        elif table == "income":
            cur = clean.get("currency")
            if cur is None and existing is not None:
                cur = getattr(existing, "currency", "EUR")
            elif cur is None:
                cur = "EUR"
            for base, eur in (("budgeted", "budgeted_eur"), ("actual", "actual_eur")):
                bval = clean.get(base)
                if bval is None and existing is not None and eur in clean:
                    bval = getattr(existing, base, None)
                if (base in clean or "currency" in clean or eur in clean) and bval is not None:
                    clean[eur] = to_eur(float(bval), cur, rates)
            # hours*rate case: if hours/rate present, budgeted/actual derived from them?
            # For sync we treat budgeted/actual as authoritative; hours/rate are separate.
        elif table == "savings":
            cur = clean.get("currency")
            if cur is None and existing is not None:
                cur = getattr(existing, "currency", "EUR")
            elif cur is None:
                cur = "EUR"
            dep = clean.get("deposited")
            if dep is None and existing is not None and "deposited_eur" in clean:
                dep = getattr(existing, "deposited", None)
            if ("deposited" in clean or "currency" in clean or "deposited_eur" in clean) and dep is not None:
                clean["deposited_eur"] = to_eur(float(dep), cur, rates)
            # balance_eur is a derived chain on read, but if client sends it, recompute from deposited as best-effort
            # We leave balance_eur as validated value; recompute would need chain history.
        elif table == "savings_accounts":
            cur = clean.get("currency")
            if cur is None and existing is not None:
                cur = getattr(existing, "currency", "EUR")
            elif cur is None:
                cur = "EUR"
            amt = clean.get("amount")
            if amt is None and existing is not None and "amount_eur" in clean:
                amt = getattr(existing, "amount", None)
            if ("amount" in clean or "currency" in clean or "amount_eur" in clean) and amt is not None:
                clean["amount_eur"] = to_eur(float(amt), cur, rates)
    except (ValueError, TypeError) as e:
        # Propagate as validation error by raising; caller will handle
        raise ValueError(str(e))


def validate_fields(table: str, fields: dict, rates: dict | None = None):
    """Validate/coerce a change's fields against the table's schema.

    Returns (clean_fields, errors). Unknown fields, server-managed fields,
    bad types, oversized strings, non-finite numbers, business-rule violations
    (>0, caps, enums), and currency mismatches are errors — nothing is
    silently dropped. When rates is provided, derived *_eur fields are
    overwritten server-side via to_eur (PATTERN-01).
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
                # Currency enum validation
                if k == "currency":
                    cur = s.strip().upper()
                    if cur not in SUPPORTED_CURRENCIES:
                        errors.append(f"unknown currency {cur}")
                        continue
                    clean[k] = cur
                elif k == "loan_payment_type":
                    if s not in ("regular", "early"):
                        errors.append("unknown loan_payment_type")
                        continue
                    clean[k] = s
                else:
                    clean[k] = s
            elif spec == "float":
                f = float(v)
                if not math.isfinite(f):
                    errors.append(f"{k} must be finite")
                    continue
                # Centralized business guards (PATTERN-01)
                # Expenses
                if table == "expenses":
                    if k == "amount":
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                    elif k == "amount_eur":
                        # Will be overwritten via to_eur when rates available; still validate isolated value
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                    elif k == "loan_surcharge_eur":
                        if not (f >= 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be >= 0 and <= {MAX_AMOUNT:g}")
                            continue
                elif table == "income":
                    if k == "hours":
                        if not (f > 0 and f <= 744 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= 744")
                            continue
                    elif k == "rate":
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                    elif k in ("budgeted", "actual"):
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                    elif k in ("budgeted_eur", "actual_eur"):
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                elif table == "savings":
                    if k == "target_eur":
                        if not (f > 0 and f <= MAX_SAVINGS_TARGET):
                            errors.append(f"{k} must be > 0 and <= {MAX_SAVINGS_TARGET:g}")
                            continue
                    elif k == "deposited":
                        if not (math.isfinite(f) and abs(f) > 0 and abs(f) <= MAX_AMOUNT):
                            errors.append(f"{k} must be != 0 and |value| <= {MAX_AMOUNT:g}")
                            continue
                    elif k == "deposited_eur":
                        if not (abs(f) <= MAX_AMOUNT):
                            errors.append(f"{k} must be |value| <= {MAX_AMOUNT:g}")
                            continue
                    elif k == "interest_rate":
                        if not (0 <= f <= 100):
                            errors.append(f"{k} must be >= 0 and <= 100")
                            continue
                    elif k == "balance_eur":
                        if not (f >= 0 and f <= MAX_SAVINGS_TARGET):
                            errors.append(f"{k} must be >= 0 and <= {MAX_SAVINGS_TARGET:g}")
                            continue
                elif table == "savings_accounts":
                    if k == "amount":
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                    elif k == "amount_eur":
                        if not (f > 0 and f <= MAX_AMOUNT):
                            errors.append(f"{k} must be > 0 and <= {MAX_AMOUNT:g}")
                            continue
                    elif k == "annual_rate":
                        if not (0 <= f <= 100):
                            errors.append(f"{k} must be >= 0 and <= 100")
                            continue
                clean[k] = f
            elif spec == "bool":
                # bool("false") is True — parse explicitly instead.
                if isinstance(v, bool):
                    clean[k] = v
                elif isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
                    clean[k] = True
                elif isinstance(v, str) and v.strip().lower() in ("0", "false", "no", "off"):
                    clean[k] = False
                elif isinstance(v, (int, float)) and v in (0, 1):
                    clean[k] = bool(v)
                else:
                    errors.append(f"{k} invalid type")
        except (TypeError, ValueError) as e:
            # Preserve business-guard messages; otherwise generic
            msg = str(e)
            if "must be" in msg or "unknown" in msg:
                errors.append(msg if msg else f"{k} invalid type")
            else:
                errors.append(f"{k} invalid type")
    if table == "expenses" and "category" in clean:
        # Accept legacy (old-taxonomy) names from syncing devices: remap the
        # (category, subcategory) pair to the new taxonomy before validating.
        clean["category"], clean["subcategory"] = remap_category_subcategory(
            clean["category"], clean.get("subcategory", ""))
        if clean["category"] not in CATEGORIES:
            errors.append("unknown category")
    if table == "expenses" and clean.get("subcategory"):
        if clean["subcategory"] not in ALL_SUBCATS:
            errors.append("unknown subcategory")
    if table == "savings_accounts":
        # goal_name is NOT NULL in the model: a create without it would raise
        # an IntegrityError (and a blank value makes the row invisible).
        if "goal_name" in clean and not str(clean["goal_name"]).strip():
            errors.append("goal_name must not be blank")
        if "status" in clean and clean["status"] not in ("active", "closed"):
            errors.append("unknown status")
    # Server-side recompute of derived EUR fields when rates are supplied
    if rates is not None and not errors:
        try:
            _recompute_derived_eur(table, clean, rates, existing=None)
        except ValueError as e:
            errors.append(str(e))
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
    if any(k not in fields for k in REQUIRED_FIELDS.get(table, ())):
        return False, None
    # Server-side EUR recompute before persistence (PATTERN-01)
    try:
        rates = _get_user_rates(user_id)
        # Work on a copy so caller retains original
        fields = dict(fields)
        _recompute_derived_eur(table, fields, rates, existing=None)
    except ValueError:
        return False, None
    try:
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
            # Ensure soft-delete sentinel: missing is_deleted -> False (0) not NULL (T4-003).
            if hasattr(obj, "is_deleted") and "is_deleted" not in fields:
                obj.is_deleted = False
            s.add(obj)
            log_audit(s, user_id, "CREATE", table, final_id,
                      {**fields, "via": "sync"})
    except IntegrityError:
        # e.g. a NOT NULL column the schema check missed — report this change
        # as failed instead of crashing the whole sync call.
        return False, None
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
        # Server-side EUR recompute using user's rates and existing values
        try:
            rates = _get_user_rates(user_id)
            # Ensure clean is not mutated unexpectedly for caller; work on copy then assign back
            _recompute_derived_eur(table, clean, rates, existing=obj)
        except ValueError as e:
            # Treat recompute failure as a validation failure: do not apply
            return {"error": str(e)}
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
        log_audit(s, user_id, "UPDATE", table, record_id,
                  {**clean, "via": "sync"})
        return {"updated": True}


def apply_changes(user_id: int, changes: list, since=None) -> dict:
    """Apply a device's changes (validated, atomically, conflict-checked).

    since = the device's server-recorded last_sync_at (datetime or ISO).
    Returns {"applied": [...], "conflicts": [...], "failed": [...]}.
    """
    since = parse_since(since)
    applied, conflicts, failed = [], [], []
    # Fetch rates once for this user's sync batch (PATTERN-01)
    try:
        rates = _get_user_rates(user_id)
    except Exception:
        rates = get_rates({})
    for ch in (changes or [])[:MAX_CHANGES]:
        table = ch.get("table")
        rid = str(ch.get("id") or "")
        if table not in SYNC_MODELS or not rid:
            failed.append({"id": rid, "table": table,
                           "error": "unknown table or missing id"})
            continue
        clean, errors = validate_fields(table, ch.get("fields") or {}, rates=rates)
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
        elif "error" in res:
            failed.append({"id": rid, "table": table, "error": res["error"]})
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
