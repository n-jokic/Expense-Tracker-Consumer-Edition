"""
db.py — SQLite + SQLAlchemy database layer for Expense Tracker v3.
All data operations are scoped to user_id.
"""

import os
import uuid
import json
import secrets
import string
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timezone, timedelta

import pandas as pd
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, JSON,
    DateTime, Date, ForeignKey, Text, event, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ── Engine & session setup ────────────────────────────────────────────────────

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# Tests override DB_PATH/BACKUP_DIR before importing this module (see
# tests/conftest.py) so the live database is never touched by the suite.
DB_PATH  = os.environ.get("DB_PATH") or os.path.join(BASE_DIR, "expense_tracker.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(BASE_DIR, "backups")

# Override with e.g. postgresql+psycopg2://user:pass@host/db when hosting.
DATABASE_URL = os.environ.get("DATABASE_URL")

_engine  = None
_Session = None
Base     = declarative_base()


def _utcnow():
    return datetime.now(timezone.utc)


def get_engine():
    global _engine
    if _engine is None:
        if DATABASE_URL:
            _engine = create_engine(DATABASE_URL)
        else:
            os.makedirs(BASE_DIR, exist_ok=True)
            _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
            # Enable WAL mode for better concurrent access (SQLite only)
            @event.listens_for(_engine, "connect")
            def set_wal(dbapi_conn, _):
                dbapi_conn.execute("PRAGMA journal_mode=WAL")
                dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return _engine


def _get_session_factory():
    global _Session
    if _Session is None:
        # expire_on_commit=False: rows are converted to dicts/DataFrames AFTER
        # the session closes, so refreshing expired attributes on detached
        # instances would raise DetachedInstanceError.
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _Session


@contextmanager
def get_session():
    Session = _get_session_factory()
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Models ────────────────────────────────────────────────────────────────────

class Household(Base):
    __tablename__ = "households"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String, nullable=False)
    invite_code = Column(String, unique=True)
    created_at  = Column(DateTime, default=_utcnow)
    members     = relationship("User", back_populates="household")


class User(Base):
    __tablename__ = "users"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    username            = Column(String, unique=True, nullable=False)
    email               = Column(String, unique=True, nullable=False)
    password_hash       = Column(String, nullable=False)
    display_name        = Column(String)
    household_id        = Column(Integer, ForeignKey("households.id"), nullable=True)
    is_admin            = Column(Boolean, default=False)
    created_at          = Column(DateTime, default=_utcnow)
    onboarding_complete = Column(Boolean, default=False)
    data_revision       = Column(Integer, default=0)  # shared cache revision
    household           = relationship("Household", back_populates="members")


class Expense(Base):
    __tablename__ = "expenses"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    date         = Column(Date)
    category     = Column(String)
    subcategory  = Column(String, default="")
    description  = Column(String)
    amount       = Column(Float, default=0.0)
    currency     = Column(String, default="EUR")
    amount_eur   = Column(Float, default=0.0)
    recurring    = Column(Boolean, default=False)
    rec_template_id = Column(String, nullable=True)  # links to recurring.id when logged from a template
    loan_id      = Column(String, nullable=True)     # links to loans.id when logged as a loan payment
    notes        = Column(String, default="")
    # ML suggestion telemetry (measurement-first): which pipeline suggested
    # the category, its confidence/model version, the normalized merchant,
    # and whether the user accepted or corrected it.
    suggest_source        = Column(String, nullable=True)   # classifier | keywords
    suggest_confidence    = Column(Float, nullable=True)
    suggest_model_version = Column(Integer, nullable=True)
    suggest_merchant      = Column(String, nullable=True)
    suggest_accepted      = Column(Boolean, nullable=True)
    # Subcategory suggestion telemetry (Phase B ML).
    suggest_subcategory           = Column(String, nullable=True)
    suggest_subcategory_confidence = Column(Float, nullable=True)
    suggest_subcategory_source     = Column(String, nullable=True)  # classifier | keywords
    suggest_subcategory_accepted   = Column(Boolean, nullable=True)
    is_deleted   = Column(Boolean, default=False)
    deleted_at   = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=_utcnow)
    updated_at   = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Income(Base):
    __tablename__ = "income"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    date         = Column(Date)
    source       = Column(String)
    income_type  = Column(String, default="Other")  # Salary | Hourly | Bonus / Raise | Freelance | Investment | Rental | Other
    hours        = Column(Float, nullable=True)     # for hourly work
    rate         = Column(Float, nullable=True)     # hourly rate (original currency)
    budgeted     = Column(Float, default=0.0)
    actual       = Column(Float, default=0.0)
    currency     = Column(String, default="EUR")
    budgeted_eur = Column(Float, default=0.0)
    actual_eur   = Column(Float, default=0.0)
    notes        = Column(String, default="")
    is_deleted   = Column(Boolean, default=False)
    deleted_at   = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=_utcnow)
    updated_at   = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Savings(Base):
    __tablename__ = "savings"
    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    date          = Column(Date)
    goal_name     = Column(String)
    target_eur    = Column(Float, default=0.0)
    deposited     = Column(Float, default=0.0)
    currency      = Column(String, default="EUR")
    deposited_eur = Column(Float, default=0.0)
    interest_rate = Column(Float, default=0.0)
    balance_eur   = Column(Float, default=0.0)
    notes         = Column(String, default="")
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=_utcnow)
    updated_at    = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class SavingsAccount(Base):
    """A fixed-term deposit ('savings account') under a savings goal:
    money locked until a maturity date at a fixed annual rate."""
    __tablename__ = "savings_accounts"
    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    goal_name     = Column(String, nullable=False)
    name          = Column(String, default="")
    amount        = Column(Float, default=0.0)      # deposit, original currency
    currency      = Column(String, default="EUR")
    amount_eur    = Column(Float, default=0.0)      # deposit, EUR
    annual_rate   = Column(Float, default=0.0)      # percent, compounded monthly
    start_date    = Column(Date)
    maturity_date = Column(Date)
    status        = Column(String, default="active")  # active | closed
    notes         = Column(String, default="")
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=_utcnow)
    updated_at    = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        # One budget row per (user, year, month, category, subcategory);
        # subcategory "" = entire category. Overlaps are never summed.
        UniqueConstraint("user_id", "year", "month", "category", "subcategory",
                         name="uq_budget_scope"),
    )
    id           = Column(Integer, primary_key=True, autoincrement=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    year         = Column(Integer)
    month        = Column(Integer)
    category     = Column(String)
    subcategory  = Column(String, default="")
    budgeted_eur = Column(Float, default=0.0)


class Recurring(Base):
    __tablename__ = "recurring"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    category    = Column(String)
    subcategory = Column(String, default="")
    description = Column(String)
    amount      = Column(Float, default=0.0)
    currency    = Column(String, default="EUR")
    amount_eur  = Column(Float, default=0.0)
    due_day     = Column(Integer, nullable=True)   # day of month (1-31); None = no due day
    start_month = Column(String, nullable=True)    # "YYYY-MM" first active month; None = always
    notes       = Column(String, default="")
    active      = Column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    action     = Column(String)
    table_name = Column(String)
    record_id  = Column(String)
    details    = Column(Text)
    timestamp  = Column(DateTime, default=_utcnow)
    ip_address = Column(String, nullable=True)


class BigPurchase(Base):
    __tablename__ = "big_purchases"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    name        = Column(String)
    category    = Column(String, default="Other")
    price       = Column(Float, default=0.0)
    currency    = Column(String, default="EUR")
    price_eur   = Column(Float, default=0.0)
    usage_hours = Column(Float, default=0.0)   # expected use, hours per month
    importance  = Column(Integer, default=3)    # 1-5
    status      = Column(String, default="wishlist")  # wishlist | saving | bought
    notes       = Column(String, default="")
    created_at  = Column(DateTime, default=_utcnow)


class Loan(Base):
    __tablename__ = "loans"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    name        = Column(String)
    principal   = Column(Float, default=0.0)
    currency    = Column(String, default="EUR")
    principal_eur = Column(Float, default=0.0)
    annual_rate = Column(Float, default=0.0)    # percent
    start_date  = Column(Date)
    term_months = Column(Integer, default=12)
    payment_day = Column(Integer, default=1)
    status      = Column(String, default="active")  # active | paid_off
    notes       = Column(String, default="")
    created_at  = Column(DateTime, default=_utcnow)


class Holding(Base):
    __tablename__ = "holdings"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    symbol       = Column(String)                # normalized, e.g. AAPL, VWCE.DE
    name         = Column(String, default="")
    quantity     = Column(Float, default=0.0)
    currency     = Column(String, default="EUR")
    cost_total   = Column(Float, default=0.0)    # invested, original currency
    cost_eur     = Column(Float, default=0.0)    # invested, EUR
    last_price   = Column(Float, default=0.0)    # last known price (original currency)
    last_price_date = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=_utcnow)


class HoldingPrice(Base):
    __tablename__ = "holding_prices"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    holding_id = Column(String, ForeignKey("holdings.id"), nullable=False)
    date       = Column(Date, default=date.today)
    price      = Column(Float, default=0.0)       # price in the holding's currency
    quantity   = Column(Float, default=0.0)       # quantity AT SNAPSHOT TIME
    rate       = Column(Float, default=0.0)       # 1 EUR = X in holding currency at snapshot time
    value_eur  = Column(Float, default=0.0)       # quantity * price / rate


class Device(Base):
    __tablename__ = "devices"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    name         = Column(String, default="Phone")
    pairing_code = Column(String, nullable=True)   # shown to the user; cleared after pairing
    token_hash   = Column(String, nullable=True)   # sha256 of the device token
    token_expires_at = Column(DateTime, nullable=True)  # token validity window
    created_at   = Column(DateTime, default=_utcnow)
    last_sync_at = Column(DateTime, nullable=True)


class UserMilestone(Base):
    __tablename__ = "user_milestones"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    milestone_id = Column(String, nullable=False)
    earned_at    = Column(DateTime, default=_utcnow)


class SyncConflict(Base):
    __tablename__ = "sync_conflicts"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    table_name   = Column(String, nullable=False)
    record_id    = Column(String, nullable=False)
    device_value = Column(JSON, nullable=True)   # what the device wanted to write
    server_value = Column(JSON, nullable=True)   # what the server currently holds
    created_at   = Column(DateTime, default=_utcnow)
    resolved     = Column(Boolean, default=False)


class UserSettings(Base):
    __tablename__ = "user_settings"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    exchange_rate    = Column(Float, default=117.0)
    default_currency = Column(String, default="EUR")
    monthly_budget   = Column(Float, default=0.0)
    # Per-currency exchange rates: {"USD": 1.08, "RSD": 117.0, ...} (1 EUR = X)
    currency_rates   = Column(JSON, nullable=True)
    # When currency_rates was last refreshed from the live-rate API
    rates_updated_at = Column(DateTime, nullable=True)
    # Fixed salary setup (income page)
    salary_amount    = Column(Float, default=0.0)
    salary_currency  = Column(String, default="EUR")
    salary_day       = Column(Integer, default=1)
    salary_active    = Column(Boolean, default=False)
    # Notifications
    bill_reminder_days = Column(Integer, default=2)
    weekly_summary      = Column(Boolean, default=False)
    weekly_summary_last_sent = Column(Date, nullable=True)
    # Big purchases math
    hourly_rate      = Column(Float, default=0.0)
    # Fun money & travel budgets
    fun_money        = Column(Float, default=0.0)          # monthly allowance, EUR
    fun_categories   = Column(JSON, nullable=True)          # category names in the fun pool
    fun_bonus_amount = Column(Float, default=0.0)          # reward bonus, EUR
    fun_bonus_month  = Column(String, nullable=True)        # "YYYY-MM" the bonus applies to
    travel_budget    = Column(Float, default=0.0)          # yearly allowance, EUR
    travel_categories = Column(JSON, nullable=True)         # "Category › Subcategory" pairs
    sent_markers     = Column(JSON, nullable=True)          # per-month alert dedupe markers
    email_alerts     = Column(Boolean, default=False)
    alert_email      = Column(String, nullable=True)
    smtp_host        = Column(String, nullable=True)
    smtp_port        = Column(Integer, default=587)
    smtp_user        = Column(String, nullable=True)
    smtp_password_enc = Column(String, nullable=True)


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate(engine)


def _add_missing_columns(engine, table: str, columns: dict):
    """Additive migration: ALTER TABLE for each missing column (SQLite + Postgres)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    for name, ddl in columns.items():
        if name not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _migrate(engine):
    """Lightweight additive migrations for installs created before new columns."""
    _add_missing_columns(engine, "user_settings", {
        "currency_rates": "JSON",
        "rates_updated_at": "TIMESTAMP",
        "fun_money": "FLOAT DEFAULT 0",
        "fun_categories": "JSON",
        "fun_bonus_amount": "FLOAT DEFAULT 0",
        "fun_bonus_month": "VARCHAR",
        "travel_budget": "FLOAT DEFAULT 0",
        "travel_categories": "JSON",
        "sent_markers": "JSON",
        "salary_amount": "FLOAT DEFAULT 0",
        "salary_currency": "VARCHAR DEFAULT 'EUR'",
        "salary_day": "INTEGER DEFAULT 1",
        "salary_active": "BOOLEAN DEFAULT 0",
        "bill_reminder_days": "INTEGER DEFAULT 2",
        "weekly_summary": "BOOLEAN DEFAULT 0",
        "weekly_summary_last_sent": "DATE",
        "hourly_rate": "FLOAT DEFAULT 0",
    })
    _add_missing_columns(engine, "income", {
        "income_type": "VARCHAR DEFAULT 'Other'",
        "hours": "FLOAT",
        "rate": "FLOAT",
    })
    _add_missing_columns(engine, "recurring", {
        "due_day": "INTEGER",
        "start_month": "VARCHAR",
    })
    _add_missing_columns(engine, "expenses", {
        "rec_template_id": "VARCHAR",
        "loan_id": "VARCHAR",
        "updated_at": "TIMESTAMP",
        "suggest_source": "VARCHAR",
        "suggest_confidence": "FLOAT",
        "suggest_model_version": "INTEGER",
        "suggest_merchant": "VARCHAR",
        "suggest_accepted": "BOOLEAN",
        "suggest_subcategory": "VARCHAR",
        "suggest_subcategory_confidence": "FLOAT",
        "suggest_subcategory_source": "VARCHAR",
        "suggest_subcategory_accepted": "BOOLEAN",
    })
    _add_missing_columns(engine, "income", {
        "updated_at": "TIMESTAMP",
    })
    _add_missing_columns(engine, "savings", {
        "updated_at": "TIMESTAMP",
    })
    _add_missing_columns(engine, "users", {
        "data_revision": "INTEGER DEFAULT 0",
    })
    _add_missing_columns(engine, "holding_prices", {
        "quantity": "FLOAT",
        "rate": "FLOAT",
        "value_eur": "FLOAT",
    })
    _add_missing_columns(engine, "devices", {
        "token_expires_at": "TIMESTAMP",
    })
    _migrate_taxonomy(engine)
    _migrate_settings_taxonomy()
    _enforce_budget_scopes(engine)
    _enforce_pairing_code_uniqueness(engine)


def _migrate_taxonomy(engine):
    """Idempotent taxonomy rewrite: only rows whose category/subcategory pair
    IS an old name are rewritten, so re-runs naturally match nothing."""
    from sqlalchemy import inspect, text
    from utils import TAXONOMY_MIGRATION, CATEGORY_RENAMES

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    # Expenses and recurring carry (category, subcategory); rewrite in place.
    for table in ("expenses", "recurring"):
        if table not in tables:
            continue
        with engine.begin() as conn:
            for old_cat, old_sub, new_cat, new_sub in TAXONOMY_MIGRATION:
                if (old_cat, old_sub) == (new_cat, new_sub):
                    continue  # identity mapping — no-op
                conn.execute(text(
                    f"UPDATE {table} SET category=:nc, subcategory=:ns "
                    f"WHERE category=:oc AND subcategory=:os"),
                    {"nc": new_cat, "ns": new_sub, "oc": old_cat, "os": old_sub})

    # Budgets: two old scopes can collapse into one new scope (e.g. a
    # whole-category "Food & Dining" row and a "Food & Dining › Groceries"
    # row both become "Groceries › Groceries"). Merge by summing so the
    # unique scope constraint is never violated.
    if "budgets" in tables:
        _migrate_budgets_taxonomy(engine)

    # big_purchases stores only a category.
    if "big_purchases" in tables:
        with engine.begin() as conn:
            for old_cat, new_cat in CATEGORY_RENAMES.items():
                if old_cat != new_cat:
                    conn.execute(text(
                        "UPDATE big_purchases SET category=:nc WHERE category=:oc"),
                        {"nc": new_cat, "oc": old_cat})


def _migrate_budgets_taxonomy(engine):
    """Rewrite budget scopes, merging rows whose remapped scopes collide."""
    from sqlalchemy import text
    from utils import remap_category_subcategory

    with engine.begin() as conn:
        rows = [dict(r) for r in conn.execute(text(
            "SELECT id, user_id, year, month, category, subcategory, budgeted_eur "
            "FROM budgets")).mappings()]

    # Fast path: nothing to do when no scope actually changes.
    if not any(remap_category_subcategory(r["category"], r["subcategory"])
               != (r["category"], r["subcategory"]) for r in rows):
        return

    # Group every row by its FINAL scope and merge (keep newest id, sum value).
    scopes = {}
    for r in rows:
        nc, ns = remap_category_subcategory(r["category"], r["subcategory"])
        key = (r["user_id"], r["year"], r["month"], nc, ns)
        if key not in scopes:
            scopes[key] = {"ids": [], "total": 0.0}
        scopes[key]["ids"].append(r["id"])
        scopes[key]["total"] += float(r["budgeted_eur"] or 0.0)

    with engine.begin() as conn:
        for (uid, yr, mo, nc, ns), g in scopes.items():
            keep = max(g["ids"])  # newest row survives, matching dedupe semantics
            for rid in g["ids"]:
                if rid != keep:
                    conn.execute(text("DELETE FROM budgets WHERE id=:id"),
                                 {"id": rid})
            conn.execute(text(
                "UPDATE budgets SET category=:nc, subcategory=:ns, "
                "budgeted_eur=:v WHERE id=:id"),
                {"nc": nc, "ns": ns, "v": g["total"], "id": keep})


def _migrate_settings_taxonomy():
    """Rewrite user_settings fun/travel category pools to the new taxonomy."""
    from utils import remap_fun_categories, remap_travel_categories
    with get_session() as s:
        for obj in s.query(UserSettings).all():
            if isinstance(obj.fun_categories, list):
                new_fc = remap_fun_categories(obj.fun_categories)
                if new_fc != obj.fun_categories:
                    obj.fun_categories = new_fc
            if isinstance(obj.travel_categories, list):
                new_tc = remap_travel_categories(obj.travel_categories)
                if new_tc != obj.travel_categories:
                    obj.travel_categories = new_tc


def _enforce_pairing_code_uniqueness(engine):
    """Pairing codes must be unique while active (NULL once the device has
    paired, so a partial index keeps unpaired devices collision-free)."""
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    if "devices" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_devices_pairing_code"
            " ON devices (pairing_code) WHERE pairing_code IS NOT NULL"))


# ── Shared cache revision ────────────────────────────────────────────────────

def get_data_revision(user_id: int) -> int:
    """The user's shared data revision — every session reads the same value,
    so cached readers invalidate for ALL browser sessions, household members,
    and background jobs the moment any write bumps it."""
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        return int(getattr(u, "data_revision", 0) or 0) if u else 0


def bump_data_revision(user_id: int, include_household: bool = True) -> int:
    """Atomically increment the user's data revision; returns the new value.

    With include_household (default) every household member's revision is
    bumped too, so shared household caches invalidate for all members the
    moment any one of them writes.
    """
    from sqlalchemy import text
    ids = [int(user_id)]
    if include_household:
        with get_session() as s:
            u = s.query(User).filter(User.id == user_id).first()
            if u and u.household_id:
                ids = [m.id for m in s.query(User)
                       .filter(User.household_id == u.household_id).all()]
    engine = get_engine()
    with engine.begin() as conn:
        params = {f"id{i}": v for i, v in enumerate(ids)}
        placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
        conn.execute(text(
            "UPDATE users SET data_revision = COALESCE(data_revision, 0) + 1"
            f" WHERE id IN ({placeholders})"), params)
    return get_data_revision(user_id)


def _enforce_budget_scopes(engine):
    """Budget scopes are unique: (user, year, month, category, subcategory).

    Existing installs may hold overlapping rows (category-level plus
    subcategory-level for the same month) that were summed together in
    alerts. Dedupe them (keep the newest row per scope) and enforce the
    unique index.
    """
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    if "budgets" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE budgets SET subcategory = '' WHERE subcategory IS NULL"))
        conn.execute(text(
            "DELETE FROM budgets WHERE id NOT IN ("
            "  SELECT MAX(id) FROM budgets"
            "  GROUP BY user_id, year, month, category, subcategory)"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_scope"
            " ON budgets (user_id, year, month, category, subcategory)"))


# ── Audit helper ──────────────────────────────────────────────────────────────

def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return str(o)


def log_audit(session, user_id, action, table_name, record_id, details, ip=None):
    entry = AuditLog(
        user_id=user_id, action=action, table_name=table_name,
        record_id=str(record_id),
        details=json.dumps(details, default=_json_default) if isinstance(details, dict) else str(details),
        ip_address=ip
    )
    session.add(entry)


# ── DataFrame helpers ─────────────────────────────────────────────────────────

def _to_df(rows, columns):
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([{c: getattr(r, c) for c in columns} for r in rows])


def _parse_dates(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


# ── Expenses ──────────────────────────────────────────────────────────────────

_EXP_COLS = ["id","user_id","date","category","subcategory","description",
             "amount","currency","amount_eur","recurring","rec_template_id","loan_id","notes",
             "suggest_source","suggest_confidence","suggest_model_version",
             "suggest_merchant","suggest_accepted",
             "suggest_subcategory","suggest_subcategory_confidence",
             "suggest_subcategory_source","suggest_subcategory_accepted",
             "is_deleted","deleted_at","created_at","updated_at"]

def get_expenses(user_id, include_deleted=False):
    with get_session() as s:
        q = s.query(Expense).filter(Expense.user_id == user_id)
        if not include_deleted:
            q = q.filter(Expense.is_deleted == False)
        rows = q.order_by(Expense.date.desc()).all()
    df = _to_df(rows, _EXP_COLS)
    return _parse_dates(df, ["date", "created_at", "deleted_at"])


def add_expense(user_id, row):
    exp_id = str(uuid.uuid4())
    with get_session() as s:
        obj = Expense(
            id=exp_id, user_id=user_id,
            date=row.get("date"), category=row.get("category",""),
            subcategory=row.get("subcategory",""), description=row.get("description",""),
            amount=float(row.get("amount",0)), currency=row.get("currency","EUR"),
            amount_eur=float(row.get("amount_eur",0)), recurring=bool(row.get("recurring",False)),
            rec_template_id=row.get("rec_template_id"),
            loan_id=row.get("loan_id"),
            notes=row.get("notes",""),
            suggest_source=row.get("suggest_source"),
            suggest_confidence=row.get("suggest_confidence"),
            suggest_model_version=row.get("suggest_model_version"),
            suggest_merchant=row.get("suggest_merchant"),
            suggest_accepted=row.get("suggest_accepted"),
            suggest_subcategory=row.get("suggest_subcategory"),
            suggest_subcategory_confidence=row.get("suggest_subcategory_confidence"),
            suggest_subcategory_source=row.get("suggest_subcategory_source"),
            suggest_subcategory_accepted=row.get("suggest_subcategory_accepted"),
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "expenses", exp_id, row)
    return exp_id


def update_expense(user_id, expense_id, updates):
    with get_session() as s:
        obj = s.query(Expense).filter(Expense.id == expense_id, Expense.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "expenses", expense_id, updates)
    return True


def soft_delete_expense(user_id, expense_id):
    with get_session() as s:
        obj = s.query(Expense).filter(Expense.id == expense_id, Expense.user_id == user_id).first()
        if not obj:
            return False
        obj.is_deleted = True
        obj.deleted_at = _utcnow()
        log_audit(s, user_id, "DELETE", "expenses", expense_id, {"soft": True})
    return True


def restore_expense(user_id, expense_id):
    with get_session() as s:
        obj = s.query(Expense).filter(Expense.id == expense_id, Expense.user_id == user_id).first()
        if not obj:
            return False
        obj.is_deleted = False
        obj.deleted_at = None
        log_audit(s, user_id, "RESTORE", "expenses", expense_id, {})
    return True


# ── Income ────────────────────────────────────────────────────────────────────

_INC_COLS = ["id","user_id","date","source","income_type","hours","rate",
             "budgeted","actual","currency","budgeted_eur","actual_eur",
             "notes","is_deleted","deleted_at","created_at","updated_at"]

# Legacy installs stored the type inside `source`; map those labels on read.
_LEGACY_INCOME_TYPES = {
    "Primary Salary": "Salary",
    "Freelance / Side Income": "Freelance",
    "Investment Returns": "Investment",
    "Rental Income": "Rental",
}


def _fill_income_types(df: pd.DataFrame) -> pd.DataFrame:
    if not df.empty and "income_type" in df.columns:
        df["income_type"] = (df["income_type"]
                             .fillna(df["source"].map(_LEGACY_INCOME_TYPES))
                             .fillna("Other"))
    return df


def get_income(user_id, include_deleted=False):
    with get_session() as s:
        q = s.query(Income).filter(Income.user_id == user_id)
        if not include_deleted:
            q = q.filter(Income.is_deleted == False)
        rows = q.order_by(Income.date.desc()).all()
    df = _to_df(rows, _INC_COLS)
    df = _parse_dates(df, ["date", "created_at", "deleted_at"])
    return _fill_income_types(df)


def add_income(user_id, row):
    inc_id = str(uuid.uuid4())
    with get_session() as s:
        obj = Income(
            id=inc_id, user_id=user_id,
            date=row.get("date"), source=row.get("source",""),
            income_type=row.get("income_type","Other"),
            hours=row.get("hours"), rate=row.get("rate"),
            budgeted=float(row.get("budgeted",0)), actual=float(row.get("actual",0)),
            currency=row.get("currency","EUR"),
            budgeted_eur=float(row.get("budgeted_eur",0)),
            actual_eur=float(row.get("actual_eur",0)),
            notes=row.get("notes","")
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "income", inc_id, row)
    return inc_id


def update_income(user_id, income_id, updates):
    """Edit an income entry. The row stores its own original values — edits
    rewrite this row only, never any other history."""
    with get_session() as s:
        obj = s.query(Income).filter(Income.id == income_id,
                                     Income.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "income", income_id, updates)
    return True


def soft_delete_income(user_id, income_id):
    with get_session() as s:
        obj = s.query(Income).filter(Income.id == income_id, Income.user_id == user_id).first()
        if not obj:
            return False
        obj.is_deleted = True
        obj.deleted_at = _utcnow()
        log_audit(s, user_id, "DELETE", "income", income_id, {"soft": True})
    return True


def restore_income(user_id, income_id):
    with get_session() as s:
        obj = s.query(Income).filter(Income.id == income_id, Income.user_id == user_id).first()
        if not obj:
            return False
        obj.is_deleted = False
        obj.deleted_at = None
        log_audit(s, user_id, "RESTORE", "income", income_id, {})
    return True


# ── Savings ───────────────────────────────────────────────────────────────────

_SAV_COLS = ["id","user_id","date","goal_name","target_eur","deposited","currency",
             "deposited_eur","interest_rate","balance_eur","notes",
             "is_deleted","deleted_at","created_at","updated_at"]

def get_savings(user_id, include_deleted=False):
    with get_session() as s:
        q = s.query(Savings).filter(Savings.user_id == user_id)
        if not include_deleted:
            q = q.filter(Savings.is_deleted == False)
        rows = q.order_by(Savings.date.asc()).all()
    df = _to_df(rows, _SAV_COLS)
    df = _parse_dates(df, ["date", "created_at", "deleted_at"])
    return _recompute_savings_balances(df, asof=date.today())


def _recompute_savings_balances(df: pd.DataFrame, asof: date | None = None) -> pd.DataFrame:
    """Rebuild each goal's running balance from its deposit history.

    Interest is compounded monthly on the elapsed months between consecutive
    deposits (using the earlier deposit's interest rate), so the balance stays
    consistent even when rows are edited, deleted, or two deposits land in the
    same month. Withdrawals (negative deposits) are supported; the balance is
    clamped at 0.

    With `asof` (default None = no tail accrual), each goal's LAST entry is
    also compounded forward from its date to `asof` using its own interest
    rate, so the displayed balance is the value TODAY — a goal with a single
    deposit still earns interest over time.
    """
    if df.empty:
        return df
    df = df.copy()
    for goal in df["goal_name"].dropna().unique():
        rows = df[df["goal_name"] == goal].sort_values("date", na_position="first")
        prev_date = None
        prev_rate = 0.0
        bal = 0.0
        first = True
        last_idx = None
        for idx in rows.index:
            r = df.loc[idx]
            dep = float(r["deposited_eur"] or 0.0)
            d = r["date"]
            if first:
                bal = dep
                first = False
            elif pd.isna(d) or pd.isna(prev_date):
                # No usable date info — just add the deposit without interest.
                bal += dep
            else:
                months = (d.year - prev_date.year) * 12 + (d.month - prev_date.month)
                if months > 0 and prev_rate > 0:
                    bal = bal * ((1 + prev_rate / 100 / 12) ** months)
                bal += dep
            df.at[idx, "balance_eur"] = max(round(bal, 4), 0.0)
            if not pd.isna(d):
                prev_date = d
            prev_rate = float(r["interest_rate"] or 0.0)
            last_idx = idx
        # Tail accrual: roll the last entry forward to `asof` (today) so the
        # balance reflects the current value, not the last deposit's date.
        prev_d = prev_date.date() if isinstance(prev_date, pd.Timestamp) else prev_date
        if (asof is not None and last_idx is not None and prev_d is not None
                and prev_rate > 0 and asof > prev_d):
            months = (asof.year - prev_d.year) * 12 + (asof.month - prev_d.month)
            if months > 0:
                bal = bal * ((1 + prev_rate / 100 / 12) ** months)
                df.at[last_idx, "balance_eur"] = max(round(bal, 4), 0.0)
    return df


def add_savings(user_id, row):
    sav_id = str(uuid.uuid4())
    with get_session() as s:
        obj = Savings(
            id=sav_id, user_id=user_id,
            date=row.get("date"), goal_name=row.get("goal_name",""),
            target_eur=float(row.get("target_eur",0)),
            deposited=float(row.get("deposited",0)),
            currency=row.get("currency","EUR"),
            deposited_eur=float(row.get("deposited_eur",0)),
            interest_rate=float(row.get("interest_rate",0)),
            balance_eur=float(row.get("balance_eur",0)),
            notes=row.get("notes","")
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "savings", sav_id, row)
    return sav_id


def update_savings(user_id, savings_id, updates):
    """Edit a savings entry (date, deposited, target, interest, notes, ...).

    Note: balances are a derived chain recomputed from ALL entries on read,
    so editing an entry intentionally updates the chain from that entry
    forward — no other rows are rewritten."""
    with get_session() as s:
        obj = s.query(Savings).filter(Savings.id == savings_id,
                                      Savings.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "savings", savings_id, updates)
    return True


def soft_delete_savings(user_id, savings_id):
    with get_session() as s:
        obj = s.query(Savings).filter(Savings.id == savings_id, Savings.user_id == user_id).first()
        if not obj:
            return False
        obj.is_deleted = True
        obj.deleted_at = _utcnow()
        log_audit(s, user_id, "DELETE", "savings", savings_id, {"soft": True})
    return True


def restore_savings(user_id, savings_id):
    with get_session() as s:
        obj = s.query(Savings).filter(Savings.id == savings_id, Savings.user_id == user_id).first()
        if not obj:
            return False
        obj.is_deleted = False
        obj.deleted_at = None
        log_audit(s, user_id, "RESTORE", "savings", savings_id, {})
    return True


# ── Savings goal helpers (goal-wide edits) ────────────────────────────────────

def rename_savings_goal(user_id, old_name, new_name):
    """Rename a goal across its entries AND its term-deposit accounts.

    Returns the number of rows renamed, or 0 when the new name is empty,
    unchanged, or already taken by another goal (renaming into an existing
    goal would silently merge the two histories).
    """
    from sqlalchemy import func as _func
    new_name = (new_name or "").strip()
    if not new_name or new_name == old_name:
        return 0
    with get_session() as s:
        clash = (s.query(Savings)
                 .filter(Savings.user_id == user_id,
                         Savings.goal_name != old_name,
                         Savings.is_deleted == False,
                         _func.lower(Savings.goal_name) == new_name.lower())
                 .first())
        if clash is None:
            clash = (s.query(SavingsAccount)
                     .filter(SavingsAccount.user_id == user_id,
                             SavingsAccount.goal_name != old_name,
                             SavingsAccount.is_deleted == False,
                             _func.lower(SavingsAccount.goal_name) == new_name.lower())
                     .first())
        if clash is not None:
            log_audit(s, user_id, "RENAME", "savings_goal",
                      old_name, {"blocked": new_name, "reason": "name taken"})
            return 0
        n = 0
        for model in (Savings, SavingsAccount):
            for obj in (s.query(model)
                        .filter(model.user_id == user_id,
                                model.goal_name == old_name).all()):
                obj.goal_name = new_name
                n += 1
        log_audit(s, user_id, "RENAME", "savings_goal",
                  old_name, {"new_name": new_name, "rows": n})
    return n


def update_savings_goal(user_id, goal_name, updates):
    """Apply goal-wide values (e.g. target_eur, interest_rate) to every
    active entry of a goal. Balances are a derived chain recomputed on read,
    so editing an entry intentionally updates the chain forward."""
    with get_session() as s:
        rows = (s.query(Savings)
                .filter(Savings.user_id == user_id,
                        Savings.goal_name == goal_name,
                        Savings.is_deleted == False).all())
        for obj in rows:
            for k, v in updates.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "savings_goal", goal_name,
                  {**updates, "rows": len(rows)})
        return len(rows)


def soft_delete_savings_goal(user_id, goal_name):
    """Move every entry of a goal to the trash and remove its term accounts."""
    with get_session() as s:
        n = 0
        rows = (s.query(Savings)
                .filter(Savings.user_id == user_id,
                        Savings.goal_name == goal_name,
                        Savings.is_deleted == False).all())
        for obj in rows:
            obj.is_deleted = True
            obj.deleted_at = _utcnow()
            n += 1
        accs = (s.query(SavingsAccount)
                .filter(SavingsAccount.user_id == user_id,
                        SavingsAccount.goal_name == goal_name,
                        SavingsAccount.is_deleted == False).all())
        for obj in accs:
            obj.is_deleted = True
            obj.deleted_at = _utcnow()
        log_audit(s, user_id, "DELETE", "savings_goal", goal_name,
                  {"entries_trashed": n, "accounts_removed": len(accs)})
        return n


# ── Term-deposit accounts (under a savings goal) ──────────────────────────────

_SAV_ACC_COLS = ["id","user_id","goal_name","name","amount","currency",
                 "amount_eur","annual_rate","start_date","maturity_date",
                 "status","notes","is_deleted","deleted_at","created_at","updated_at"]


def get_savings_accounts(user_id, include_deleted=False):
    with get_session() as s:
        q = s.query(SavingsAccount).filter(SavingsAccount.user_id == user_id)
        if not include_deleted:
            q = q.filter(SavingsAccount.is_deleted == False)
        rows = (q.order_by(SavingsAccount.maturity_date.asc().nullslast(),
                           SavingsAccount.created_at.asc()).all())
    df = _to_df(rows, _SAV_ACC_COLS)
    return _parse_dates(df, ["start_date", "maturity_date", "created_at", "deleted_at"])


def add_savings_account(user_id, row):
    acc_id = str(uuid.uuid4())
    with get_session() as s:
        obj = SavingsAccount(
            id=acc_id, user_id=user_id,
            goal_name=row.get("goal_name", ""), name=row.get("name", ""),
            amount=float(row.get("amount", 0)), currency=row.get("currency", "EUR"),
            amount_eur=float(row.get("amount_eur", 0)),
            annual_rate=float(row.get("annual_rate", 0)),
            start_date=row.get("start_date"), maturity_date=row.get("maturity_date"),
            status=row.get("status", "active"), notes=row.get("notes", ""),
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "savings_accounts", acc_id, row)
    return acc_id


def update_savings_account(user_id, acc_id, updates):
    with get_session() as s:
        obj = (s.query(SavingsAccount)
               .filter(SavingsAccount.id == acc_id,
                       SavingsAccount.user_id == user_id).first())
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "savings_accounts", acc_id, updates)
    return True


def soft_delete_savings_account(user_id, acc_id):
    with get_session() as s:
        obj = (s.query(SavingsAccount)
               .filter(SavingsAccount.id == acc_id,
                       SavingsAccount.user_id == user_id).first())
        if not obj:
            return False
        obj.is_deleted = True
        obj.deleted_at = _utcnow()
        log_audit(s, user_id, "DELETE", "savings_accounts", acc_id, {"soft": True})
    return True


def restore_savings_account(user_id, acc_id):
    with get_session() as s:
        obj = (s.query(SavingsAccount)
               .filter(SavingsAccount.id == acc_id,
                       SavingsAccount.user_id == user_id).first())
        if not obj:
            return False
        obj.is_deleted = False
        obj.deleted_at = None
        log_audit(s, user_id, "RESTORE", "savings_accounts", acc_id, {})
    return True


# ── Budgets ───────────────────────────────────────────────────────────────────

_BUD_COLS = ["id","user_id","year","month","category","subcategory","budgeted_eur"]

def get_budgets(user_id):
    with get_session() as s:
        rows = s.query(Budget).filter(Budget.user_id == user_id).all()
    return _to_df(rows, _BUD_COLS)


def add_budget(user_id, row):
    """Upsert a budget row: one row per (user, year, month, category,
    subcategory) scope — saving the same scope again updates it instead of
    creating a duplicate that would be double-counted."""
    year = int(row.get("year", date.today().year))
    month = int(row.get("month", date.today().month))
    category = row.get("category", "") or ""
    subcategory = row.get("subcategory", "") or ""
    value = float(row.get("budgeted_eur", 0))
    with get_session() as s:
        obj = (s.query(Budget)
               .filter(Budget.user_id == user_id, Budget.year == year,
                       Budget.month == month, Budget.category == category,
                       Budget.subcategory == subcategory)
               .first())
        if obj:
            obj.budgeted_eur = value
            log_audit(s, user_id, "UPDATE", "budgets", obj.id, row)
            s.flush()
            return obj.id
        obj = Budget(
            user_id=user_id, year=year, month=month,
            category=category, subcategory=subcategory,
            budgeted_eur=value
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "budgets", "new", row)
        s.flush()
        return obj.id


def delete_budget(user_id, budget_id):
    with get_session() as s:
        obj = s.query(Budget).filter(Budget.id == budget_id, Budget.user_id == user_id).first()
        if not obj:
            return False
        s.delete(obj)
        log_audit(s, user_id, "DELETE", "budgets", budget_id, {})
    return True


# ── Recurring ─────────────────────────────────────────────────────────────────

_REC_COLS = ["id","user_id","category","subcategory","description",
             "amount","currency","amount_eur","due_day","start_month","notes","active"]

def get_recurring(user_id):
    with get_session() as s:
        rows = s.query(Recurring).filter(Recurring.user_id == user_id).all()
    return _to_df(rows, _REC_COLS)


def add_recurring(user_id, row):
    rec_id = str(uuid.uuid4())
    with get_session() as s:
        obj = Recurring(
            id=rec_id, user_id=user_id,
            category=row.get("category",""), subcategory=row.get("subcategory",""),
            description=row.get("description",""),
            amount=float(row.get("amount",0)), currency=row.get("currency","EUR"),
            amount_eur=float(row.get("amount_eur",0)),
            due_day=row.get("due_day"),
            start_month=row.get("start_month"),
            notes=row.get("notes",""), active=bool(row.get("active",True))
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "recurring", rec_id, row)
    return rec_id


def update_recurring(user_id, rec_id, updates):
    with get_session() as s:
        obj = s.query(Recurring).filter(Recurring.id == rec_id, Recurring.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "recurring", rec_id, updates)
    return True


# ── Big purchases ─────────────────────────────────────────────────────────────

_BIG_COLS = ["id","user_id","name","category","price","currency","price_eur",
             "usage_hours","importance","status","notes","created_at"]

BIG_STATUSES = ["wishlist", "saving", "bought"]


def get_big_purchases(user_id):
    with get_session() as s:
        rows = (s.query(BigPurchase)
                .filter(BigPurchase.user_id == user_id)
                .order_by(BigPurchase.created_at.desc()).all())
    df = _to_df(rows, _BIG_COLS)
    return _parse_dates(df, ["created_at"])


def add_big_purchase(user_id, row):
    bp_id = str(uuid.uuid4())
    with get_session() as s:
        obj = BigPurchase(
            id=bp_id, user_id=user_id,
            name=row.get("name",""), category=row.get("category","Other"),
            price=float(row.get("price",0)), currency=row.get("currency","EUR"),
            price_eur=float(row.get("price_eur",0)),
            usage_hours=float(row.get("usage_hours",0)),
            importance=int(row.get("importance",3)),
            status=row.get("status","wishlist"),
            notes=row.get("notes",""),
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "big_purchases", bp_id, row)
    return bp_id


def update_big_purchase(user_id, bp_id, updates):
    with get_session() as s:
        obj = s.query(BigPurchase).filter(BigPurchase.id == bp_id, BigPurchase.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "big_purchases", bp_id, updates)
    return True


def delete_big_purchase(user_id, bp_id):
    with get_session() as s:
        obj = s.query(BigPurchase).filter(BigPurchase.id == bp_id, BigPurchase.user_id == user_id).first()
        if not obj:
            return False
        s.delete(obj)
        log_audit(s, user_id, "DELETE", "big_purchases", bp_id, {})
    return True


# ── Loans ─────────────────────────────────────────────────────────────────────

_LOAN_COLS = ["id","user_id","name","principal","currency","principal_eur",
              "annual_rate","start_date","term_months","payment_day","status",
              "notes","created_at"]


def get_loans(user_id):
    with get_session() as s:
        rows = (s.query(Loan).filter(Loan.user_id == user_id)
                .order_by(Loan.created_at.asc()).all())
    df = _to_df(rows, _LOAN_COLS)
    return _parse_dates(df, ["start_date", "created_at"])


def add_loan(user_id, row):
    loan_id = str(uuid.uuid4())
    with get_session() as s:
        obj = Loan(
            id=loan_id, user_id=user_id,
            name=row.get("name",""),
            principal=float(row.get("principal",0)), currency=row.get("currency","EUR"),
            principal_eur=float(row.get("principal_eur",0)),
            annual_rate=float(row.get("annual_rate",0)),
            start_date=row.get("start_date"), term_months=int(row.get("term_months",12)),
            payment_day=int(row.get("payment_day",1)),
            status=row.get("status","active"), notes=row.get("notes",""),
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "loans", loan_id, row)
    return loan_id


def update_loan(user_id, loan_id, updates):
    with get_session() as s:
        obj = s.query(Loan).filter(Loan.id == loan_id, Loan.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "loans", loan_id, updates)
    return True


def delete_loan(user_id, loan_id):
    with get_session() as s:
        obj = s.query(Loan).filter(Loan.id == loan_id, Loan.user_id == user_id).first()
        if not obj:
            return False
        s.delete(obj)
        log_audit(s, user_id, "DELETE", "loans", loan_id, {})
    return True


def get_loan_payments(user_id, loan_id):
    """Payment history for a loan = non-deleted expenses linked to it."""
    with get_session() as s:
        rows = (s.query(Expense)
                .filter(Expense.user_id == user_id, Expense.loan_id == loan_id,
                        Expense.is_deleted == False)
                .order_by(Expense.date.asc()).all())
    df = _to_df(rows, _EXP_COLS)
    return _parse_dates(df, ["date", "created_at", "deleted_at"])


# ── Brokerage holdings ────────────────────────────────────────────────────────

_HOLD_COLS = ["id","user_id","symbol","name","quantity","currency",
              "cost_total","cost_eur","last_price","last_price_date","created_at"]

_PRICE_COLS = ["id","holding_id","date","price"]


def get_holdings(user_id):
    with get_session() as s:
        rows = (s.query(Holding).filter(Holding.user_id == user_id)
                .order_by(Holding.symbol.asc()).all())
    df = _to_df(rows, _HOLD_COLS)
    return _parse_dates(df, ["last_price_date", "created_at"])


def add_holding(user_id, row):
    h_id = str(uuid.uuid4())
    with get_session() as s:
        obj = Holding(
            id=h_id, user_id=user_id,
            symbol=str(row.get("symbol","")).strip().upper(),
            name=row.get("name",""),
            quantity=float(row.get("quantity",0)),
            currency=row.get("currency","EUR"),
            cost_total=float(row.get("cost_total",0)),
            cost_eur=float(row.get("cost_eur",0)),
            last_price=float(row.get("last_price",0)),
            last_price_date=row.get("last_price_date"),
        )
        s.add(obj)
        log_audit(s, user_id, "CREATE", "holdings", h_id, row)
    return h_id


def update_holding(user_id, h_id, updates):
    with get_session() as s:
        obj = s.query(Holding).filter(Holding.id == h_id, Holding.user_id == user_id).first()
        if not obj:
            return False
        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "holdings", h_id, updates)
    return True


def delete_holding(user_id, h_id):
    with get_session() as s:
        obj = s.query(Holding).filter(Holding.id == h_id, Holding.user_id == user_id).first()
        if not obj:
            return False
        s.query(HoldingPrice).filter(HoldingPrice.holding_id == h_id).delete()
        s.delete(obj)
        log_audit(s, user_id, "DELETE", "holdings", h_id, {})
    return True


def get_holding_prices(user_id):
    """Price snapshots joined with holdings, ordered by date.

    Rows created before the quantity/rate/value_eur columns existed have
    None/0 for those fields; callers fall back to today's quantity with an
    explicit "estimated" label.
    """
    with get_session() as s:
        rows = (s.query(HoldingPrice, Holding.symbol, Holding.user_id)
                .join(Holding, HoldingPrice.holding_id == Holding.id)
                .filter(Holding.user_id == user_id)
                .order_by(HoldingPrice.date.asc(), Holding.symbol.asc()).all())
    data = [{"holding_id": hp.holding_id, "symbol": sym, "date": hp.date,
             "price": hp.price, "quantity": hp.quantity, "rate": hp.rate,
             "value_eur": hp.value_eur}
            for hp, sym, _uid in rows]
    df = pd.DataFrame(data, columns=["holding_id","symbol","date","price",
                                     "quantity","rate","value_eur"])
    return _parse_dates(df, ["date"])


def add_holding_price(holding_id, price, when=None, quantity=None, rate=None):
    """Append or update a daily price snapshot (one per holding per day).

    quantity/rate are recorded so the snapshot's EUR value is exact even if
    the user later changes the holding's quantity or the currency rates.
    value_eur = quantity * price / rate (price is in the holding's currency).
    """
    when = when or date.today()
    qty = float(quantity) if quantity is not None else None
    rt = float(rate) if rate is not None else None
    value = round(qty * float(price) / rt, 4) if (qty is not None and rt) else None
    with get_session() as s:
        existing = (s.query(HoldingPrice)
                    .filter(HoldingPrice.holding_id == holding_id,
                            HoldingPrice.date == when).first())
        if existing:
            existing.price = float(price)
            if qty is not None:
                existing.quantity = qty
            if rt is not None:
                existing.rate = rt
            if value is not None:
                existing.value_eur = value
        else:
            s.add(HoldingPrice(holding_id=holding_id, date=when,
                               price=float(price),
                               quantity=qty or 0.0, rate=rt or 0.0,
                               value_eur=value or 0.0))
    return True


# ── Settings ──────────────────────────────────────────────────────────────────

_SETTINGS_DEFAULTS = {
    "exchange_rate": 117.0, "default_currency": "EUR", "monthly_budget": 0.0,
    "currency_rates": None, "rates_updated_at": None,
    "fun_money": 0.0, "fun_categories": None,
    "fun_bonus_amount": 0.0, "fun_bonus_month": None,
    "travel_budget": 0.0, "travel_categories": None,
    "sent_markers": None,
    "salary_amount": 0.0, "salary_currency": "EUR", "salary_day": 1,
    "salary_active": False,
    "bill_reminder_days": 2, "weekly_summary": False,
    "weekly_summary_last_sent": None, "hourly_rate": 0.0,
    "email_alerts": False, "alert_email": None, "smtp_host": None,
    "smtp_port": 587, "smtp_user": None, "smtp_password_enc": None,
}

def get_settings(user_id):
    with get_session() as s:
        obj = s.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not obj:
            return dict(_SETTINGS_DEFAULTS)
        return {k: getattr(obj, k, v) for k, v in _SETTINGS_DEFAULTS.items()}


def save_settings(user_id, settings_dict):
    with get_session() as s:
        obj = s.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not obj:
            obj = UserSettings(user_id=user_id)
            s.add(obj)
        for k, v in settings_dict.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", "user_settings", user_id, {"keys": list(settings_dict.keys())})
    return True


# ── Audit log ─────────────────────────────────────────────────────────────────

def get_audit_log(user_id, limit=200):
    with get_session() as s:
        rows = (s.query(AuditLog)
                .filter(AuditLog.user_id == user_id)
                .order_by(AuditLog.timestamp.desc())
                .limit(limit).all())
    cols = ["id","user_id","action","table_name","record_id","details","timestamp","ip_address"]
    df = _to_df(rows, cols)
    return _parse_dates(df, ["timestamp"])


# ── Households ────────────────────────────────────────────────────────────────

def _random_invite_code(length=8):
    """Cryptographically secure random code (secrets, not the PRNG)."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_household(user_id, name):
    with get_session() as s:
        for _ in range(5):  # invite codes are unique; retry on collision
            code = _random_invite_code()
            if not s.query(Household).filter(Household.invite_code == code).first():
                break
        hh = Household(name=name, invite_code=code)
        s.add(hh)
        s.flush()
        user = s.query(User).filter(User.id == user_id).first()
        if user:
            user.household_id = hh.id
        log_audit(s, user_id, "CREATE", "households", hh.id, {"name": name})
        return hh.id, code


def join_household(user_id, invite_code):
    with get_session() as s:
        hh = s.query(Household).filter(Household.invite_code == invite_code.strip().upper()).first()
        if not hh:
            return False
        user = s.query(User).filter(User.id == user_id).first()
        if user:
            user.household_id = hh.id
            log_audit(s, user_id, "UPDATE", "users", user_id, {"joined_household": hh.id})
            return True
    return False


def get_household_by_member(user_id):
    """The household a user belongs to (id, name, invite code) or None."""
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if not u or not u.household_id:
            return None
        hh = s.query(Household).filter(Household.id == u.household_id).first()
        if not hh:
            return None
        return {"id": hh.id, "name": hh.name, "invite_code": hh.invite_code}


def get_household_members(household_id):
    with get_session() as s:
        members = s.query(User).filter(User.household_id == household_id).all()
        return [{"id": m.id, "display_name": m.display_name or m.username} for m in members]


def leave_household(user_id):
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if not u:
            return False
        u.household_id = None
        log_audit(s, user_id, "UPDATE", "users", user_id, {"left_household": True})
    return True


_HH_EXP_COLS = _EXP_COLS + ["member"]


def get_household_expenses(household_id, include_deleted=False):
    with get_session() as s:
        rows = (s.query(Expense, User.display_name, User.username)
                .join(User, Expense.user_id == User.id)
                .filter(User.household_id == household_id))
        if not include_deleted:
            rows = rows.filter(Expense.is_deleted == False)
        rows = rows.order_by(Expense.date.desc()).all()
    data = []
    for exp, display_name, username in rows:
        rec = {c: getattr(exp, c) for c in _EXP_COLS}
        rec["member"] = display_name or username
        data.append(rec)
    df = pd.DataFrame(data, columns=_HH_EXP_COLS)
    return _parse_dates(df, ["date", "created_at", "deleted_at"])


# ── User helpers (used by auth.py) ────────────────────────────────────────────

def create_user(username, email, password_hash, display_name):
    with get_session() as s:
        user = User(username=username, email=email,
                    password_hash=password_hash,
                    display_name=display_name or username)
        s.add(user)
        s.flush()
        uid = user.id
        settings = UserSettings(user_id=uid)
        s.add(settings)
        log_audit(s, uid, "REGISTER", "users", uid, {"username": username})
        return uid


def get_user_by_username(username):
    # Normalise to lowercase — usernames are stored lowercase since registration normalises them
    username = username.strip().lower()
    with get_session() as s:
        u = (s.query(User)
               .filter(User.username == username)
               .first())
        if not u:
            return None
        return {
            "id": u.id, "username": u.username, "email": u.email,
            "password_hash": u.password_hash, "display_name": u.display_name or u.username,
            "household_id": u.household_id, "onboarding_complete": u.onboarding_complete,
        }


def username_exists(username):
    username = username.strip().lower()
    with get_session() as s:
        return s.query(User).filter(User.username == username).first() is not None


def email_exists(email):
    with get_session() as s:
        return s.query(User).filter(User.email == email).first() is not None


def set_onboarding_complete(user_id):
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if u:
            u.onboarding_complete = True


def update_user_password(user_id, new_hash):
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if u:
            u.password_hash = new_hash
            log_audit(s, user_id, "UPDATE", "users", user_id, {"field": "password"})
            return True
    return False


def update_user_display_name(user_id, display_name):
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if u:
            u.display_name = display_name
            return True
    return False


def delete_user_account(user_id):
    """Hard delete all user data."""
    with get_session() as s:
        holding_ids = [h.id for h in s.query(Holding).filter(Holding.user_id == user_id).all()]
        if holding_ids:
            s.query(HoldingPrice).filter(HoldingPrice.holding_id.in_(holding_ids)).delete(
                synchronize_session=False)
        s.query(Holding).filter(Holding.user_id == user_id).delete()
        s.query(Device).filter(Device.user_id == user_id).delete()
        s.query(UserMilestone).filter(UserMilestone.user_id == user_id).delete()
        s.query(SyncConflict).filter(SyncConflict.user_id == user_id).delete()
        s.query(Loan).filter(Loan.user_id == user_id).delete()
        s.query(BigPurchase).filter(BigPurchase.user_id == user_id).delete()
        s.query(Expense).filter(Expense.user_id == user_id).delete()
        s.query(Income).filter(Income.user_id == user_id).delete()
        s.query(Savings).filter(Savings.user_id == user_id).delete()
        s.query(SavingsAccount).filter(SavingsAccount.user_id == user_id).delete()
        s.query(Budget).filter(Budget.user_id == user_id).delete()
        s.query(Recurring).filter(Recurring.user_id == user_id).delete()
        s.query(UserSettings).filter(UserSettings.user_id == user_id).delete()
        s.query(AuditLog).filter(AuditLog.user_id == user_id).delete()
        s.query(User).filter(User.id == user_id).delete()
    return True


# ── Backups (SQLite only) ─────────────────────────────────────────────────────

def backup_db(force: bool = False):
    """Copy the SQLite database into BACKUP_DIR (WAL-safe).

    Without force: one backup per day (a ".last_backup" marker is checked).
    With force=True: ALWAYS take a fresh, timestamped snapshot — a second
    manual backup on the same day must capture changes made after the
    morning backup, not silently return the morning file.

    Writes are atomic: the copy lands in a temp file first, then
    os.replace() moves it into place.

    Returns the backup path, or None when not applicable / already done today.
    """
    engine = get_engine()
    if engine.dialect.name != "sqlite" or not os.path.exists(DB_PATH):
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    today = date.today()
    marker = os.path.join(BACKUP_DIR, ".last_backup")
    try:
        last = open(marker, "r", encoding="utf-8").read().strip()
    except OSError:
        last = None
    if not force and last == today.isoformat():
        return None

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR,
                        f"expense_tracker_{stamp}_{uuid.uuid4().hex[:6]}.db")
    tmp = f"{dest}.tmp"
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(tmp)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(tmp, dest)

    with open(marker, "w", encoding="utf-8") as f:
        f.write(today.isoformat())

    # Prune old backups
    try:
        from utils import BACKUP_RETENTION_DAYS
        retention = BACKUP_RETENTION_DAYS
    except Exception:
        retention = 30
    for fn in os.listdir(BACKUP_DIR):
        if not (fn.startswith("expense_tracker_") and fn.endswith(".db")):
            continue
        try:
            d = date.fromisoformat(fn[len("expense_tracker_"):][:10])
        except ValueError:
            continue
        if (today - d).days > retention:
            try:
                os.remove(os.path.join(BACKUP_DIR, fn))
            except OSError:
                pass
    return dest


# ── Devices (phone pairing / sync) ───────────────────────────────────────────

TOKEN_LIFETIME_DAYS = 90  # device tokens expire; sync refreshes the window


def _naive_utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def create_pairing_device(user_id):
    """Create a pending device row with a pairing code. Returns (device_id, code)."""
    with get_session() as s:
        code = _random_invite_code(6)
        for _ in range(5):  # codes must be unique while active
            if not s.query(Device).filter(Device.pairing_code == code).first():
                break
            code = _random_invite_code(6)
        dev = Device(user_id=user_id, pairing_code=code)
        s.add(dev)
        s.flush()
        dev_id = dev.id
    return dev_id, code


def complete_pairing(code, device_name="Phone", token=None):
    """Validate a pairing code and bind a token. Returns token or None."""
    import hashlib
    code = (code or "").strip().upper()
    with get_session() as s:
        dev = s.query(Device).filter(Device.pairing_code == code).first()
        if not dev:
            return None
        # codes expire after 10 minutes (created_at reads back as naive UTC)
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        if (now_naive - _naive_utc(dev.created_at)) > timedelta(minutes=10):
            return None
        token = token or uuid.uuid4().hex
        dev.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        dev.name = device_name or dev.name
        dev.pairing_code = None
        dev.token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) \
            + timedelta(days=TOKEN_LIFETIME_DAYS)
        return token


def get_devices(user_id):
    with get_session() as s:
        rows = s.query(Device).filter(Device.user_id == user_id,
                                      Device.token_hash.isnot(None)).all()
        return [{"id": d.id, "name": d.name, "created_at": d.created_at,
                 "last_sync_at": d.last_sync_at,
                 "token_expires_at": d.token_expires_at} for d in rows]


def device_by_token(token):
    """Resolve a device (and its user) from a raw token. Returns dict or None.

    Expired tokens are rejected (sync refreshes the window on each use)."""
    import hashlib
    h = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    with get_session() as s:
        dev = s.query(Device).filter(Device.token_hash == h).first()
        if not dev:
            return None
        exp = _naive_utc(dev.token_expires_at)
        if exp is not None and _naive_utc(datetime.now(timezone.utc)) > exp:
            return None  # token expired — re-pair the device
        return {"id": dev.id, "user_id": dev.user_id, "name": dev.name,
                "last_sync_at": _naive_utc(dev.last_sync_at)}


def touch_device_sync(device_id):
    with get_session() as s:
        dev = s.query(Device).filter(Device.id == device_id).first()
        if dev:
            dev.last_sync_at = _utcnow()
            # Sliding window: an actively syncing device stays valid.
            dev.token_expires_at = _utcnow() + timedelta(days=TOKEN_LIFETIME_DAYS)


def revoke_device(user_id, device_id):
    with get_session() as s:
        dev = s.query(Device).filter(Device.id == device_id,
                                     Device.user_id == user_id).first()
        if not dev:
            return False
        s.delete(dev)
        log_audit(s, user_id, "DELETE", "devices", device_id, {})
    return True


# ── Milestones (persistent unlocks + rewards) ────────────────────────────────

def get_earned_milestone_ids(user_id):
    with get_session() as s:
        rows = (s.query(UserMilestone)
                .filter(UserMilestone.user_id == user_id).all())
        return {m.milestone_id for m in rows}


def record_milestones(user_id, milestone_ids):
    """Persist newly earned milestones (idempotent). Returns the new ids."""
    with get_session() as s:
        existing = {m.milestone_id for m in
                    s.query(UserMilestone).filter(UserMilestone.user_id == user_id).all()}
        new_ids = [mid for mid in milestone_ids if mid not in existing]
        for mid in new_ids:
            s.add(UserMilestone(user_id=user_id, milestone_id=mid))
            log_audit(s, user_id, "CREATE", "user_milestones", mid, {})
    return new_ids


# ── Sync conflicts ───────────────────────────────────────────────────────────

def add_sync_conflict(user_id, table_name, record_id, device_value, server_value):
    with get_session() as s:
        c = SyncConflict(
            user_id=user_id, table_name=table_name, record_id=record_id,
            device_value=device_value, server_value=server_value,
        )
        s.add(c)
        s.flush()
        cid = c.id
    return cid


def get_sync_conflicts(user_id, resolved=False):
    with get_session() as s:
        rows = (s.query(SyncConflict)
                .filter(SyncConflict.user_id == user_id,
                        SyncConflict.resolved == resolved)
                .order_by(SyncConflict.created_at.desc()).all())
        return [{"id": c.id, "table_name": c.table_name, "record_id": c.record_id,
                 "device_value": c.device_value, "server_value": c.server_value,
                 "created_at": c.created_at} for c in rows]


def resolve_sync_conflict(user_id, conflict_id):
    with get_session() as s:
        c = s.query(SyncConflict).filter(SyncConflict.id == conflict_id,
                                         SyncConflict.user_id == user_id).first()
        if not c:
            return False
        c.resolved = True
        log_audit(s, user_id, "UPDATE", "sync_conflicts", conflict_id, {"resolved": True})
    return True


_SYNC_MODELS = {"expenses": Expense, "income": Income, "savings": Savings,
                "savings_accounts": SavingsAccount}


def apply_record_fields(user_id, table_name, record_id, fields) -> bool:
    """Generic field update used by 'keep device value' conflict resolution
    and the sync API. Protected fields are ignored."""
    model = _SYNC_MODELS.get(table_name)
    if not model:
        return False
    with get_session() as s:
        obj = (s.query(model)
               .filter(model.id == record_id, model.user_id == user_id).first())
        if not obj:
            return False
        for k, v in fields.items():
            if k in ("id", "user_id", "created_at", "updated_at"):
                continue
            if hasattr(obj, k):
                setattr(obj, k, v)
        log_audit(s, user_id, "UPDATE", table_name, record_id,
                  {"fields": list(fields.keys()), "via": "sync"})
    return True
