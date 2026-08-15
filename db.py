"""
db.py — SQLite + SQLAlchemy database layer for Expense Tracker v3.
All data operations are scoped to user_id.
"""

import os
import uuid
import json
import random
import string
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timezone

import pandas as pd
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, JSON,
    DateTime, Date, ForeignKey, Text, event
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ── Engine & session setup ────────────────────────────────────────────────────

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH  = os.path.join(BASE_DIR, "expense_tracker.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

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


class Budget(Base):
    __tablename__ = "budgets"
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
    price      = Column(Float, default=0.0)


class Device(Base):
    __tablename__ = "devices"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    name         = Column(String, default="Phone")
    pairing_code = Column(String, nullable=True)   # shown to the user; cleared after pairing
    token_hash   = Column(String, nullable=True)   # sha256 of the device token
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
    })
    _add_missing_columns(engine, "expenses", {
        "rec_template_id": "VARCHAR",
        "loan_id": "VARCHAR",
        "updated_at": "TIMESTAMP",
    })
    _add_missing_columns(engine, "income", {
        "updated_at": "TIMESTAMP",
    })
    _add_missing_columns(engine, "savings", {
        "updated_at": "TIMESTAMP",
    })


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
            notes=row.get("notes","")
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
    return _recompute_savings_balances(df)


def _recompute_savings_balances(df: pd.DataFrame) -> pd.DataFrame:
    """Rebuild each goal's running balance from its deposit history.

    Interest is compounded monthly on the elapsed months between consecutive
    deposits (using the earlier deposit's interest rate), so the balance stays
    consistent even when rows are edited, deleted, or two deposits land in the
    same month. Withdrawals (negative deposits) are supported; the balance is
    clamped at 0.
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


# ── Budgets ───────────────────────────────────────────────────────────────────

_BUD_COLS = ["id","user_id","year","month","category","subcategory","budgeted_eur"]

def get_budgets(user_id):
    with get_session() as s:
        rows = s.query(Budget).filter(Budget.user_id == user_id).all()
    return _to_df(rows, _BUD_COLS)


def add_budget(user_id, row):
    with get_session() as s:
        obj = Budget(
            user_id=user_id, year=int(row.get("year", date.today().year)),
            month=int(row.get("month", date.today().month)),
            category=row.get("category",""), subcategory=row.get("subcategory",""),
            budgeted_eur=float(row.get("budgeted_eur",0))
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
             "amount","currency","amount_eur","due_day","notes","active"]

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
    """Price snapshots joined with holdings, ordered by date."""
    with get_session() as s:
        rows = (s.query(HoldingPrice, Holding.symbol, Holding.user_id)
                .join(Holding, HoldingPrice.holding_id == Holding.id)
                .filter(Holding.user_id == user_id)
                .order_by(HoldingPrice.date.asc(), Holding.symbol.asc()).all())
    data = [{"holding_id": hp.holding_id,
             "symbol": sym, "date": hp.date, "price": hp.price}
            for hp, sym, _uid in rows]
    df = pd.DataFrame(data, columns=["holding_id","symbol","date","price"])
    return _parse_dates(df, ["date"])


def add_holding_price(holding_id, price, when=None):
    """Append a price snapshot (one per holding per day)."""
    when = when or date.today()
    with get_session() as s:
        existing = (s.query(HoldingPrice)
                    .filter(HoldingPrice.holding_id == holding_id,
                            HoldingPrice.date == when).first())
        if existing:
            existing.price = float(price)
        else:
            s.add(HoldingPrice(holding_id=holding_id, date=when, price=float(price)))
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
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


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
        s.query(Budget).filter(Budget.user_id == user_id).delete()
        s.query(Recurring).filter(Recurring.user_id == user_id).delete()
        s.query(UserSettings).filter(UserSettings.user_id == user_id).delete()
        s.query(AuditLog).filter(AuditLog.user_id == user_id).delete()
        s.query(User).filter(User.id == user_id).delete()
    return True


# ── Backups (SQLite only) ─────────────────────────────────────────────────────

def backup_db(force: bool = False):
    """Copy the SQLite database into data/backups once per day (WAL-safe).

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

    dest = os.path.join(BACKUP_DIR, f"expense_tracker_{today.isoformat()}.db")
    if not os.path.exists(dest):
        src = sqlite3.connect(DB_PATH)
        try:
            dst = sqlite3.connect(dest)
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

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
            d = date.fromisoformat(fn[len("expense_tracker_"):-3])
        except ValueError:
            continue
        if (today - d).days > retention:
            try:
                os.remove(os.path.join(BACKUP_DIR, fn))
            except OSError:
                pass
    return dest


# ── Devices (phone pairing / sync) ───────────────────────────────────────────

def create_pairing_device(user_id):
    """Create a pending device row with a pairing code. Returns (device_id, code)."""
    code = _random_invite_code(6)
    with get_session() as s:
        dev = Device(user_id=user_id, pairing_code=code)
        s.add(dev)
        s.flush()
        dev_id = dev.id
    return dev_id, code


def complete_pairing(code, device_name="Phone", token=None):
    """Validate a pairing code and bind a token. Returns token or None."""
    import hashlib
    from datetime import timedelta
    code = (code or "").strip().upper()
    with get_session() as s:
        dev = s.query(Device).filter(Device.pairing_code == code).first()
        if not dev:
            return None
        # codes expire after 10 minutes (created_at reads back as naive UTC)
        created = dev.created_at
        if created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        if (now_naive - created) > timedelta(minutes=10):
            return None
        token = token or uuid.uuid4().hex
        dev.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        dev.name = device_name or dev.name
        dev.pairing_code = None
        return token


def get_devices(user_id):
    with get_session() as s:
        rows = s.query(Device).filter(Device.user_id == user_id,
                                      Device.token_hash.isnot(None)).all()
        return [{"id": d.id, "name": d.name, "created_at": d.created_at,
                 "last_sync_at": d.last_sync_at} for d in rows]


def device_by_token(token):
    """Resolve a device (and its user) from a raw token. Returns dict or None."""
    import hashlib
    h = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    with get_session() as s:
        dev = s.query(Device).filter(Device.token_hash == h).first()
        if not dev:
            return None
        return {"id": dev.id, "user_id": dev.user_id, "name": dev.name}


def touch_device_sync(device_id):
    with get_session() as s:
        dev = s.query(Device).filter(Device.id == device_id).first()
        if dev:
            dev.last_sync_at = _utcnow()


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


_SYNC_MODELS = {"expenses": Expense, "income": Income, "savings": Savings}


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
