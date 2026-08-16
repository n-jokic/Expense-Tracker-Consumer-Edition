"""
pdf_import.py — Bank statement PDF parsing (generic, review-first).

Most consumer banks export statements as PDFs. We extract tables where the
PDF has them (trying a "lines" strategy first, then a more tolerant "text"
strategy for borderless PDFs), otherwise parse text lines with date/amount
patterns. Output is always the same normalized frame the CSV importer produces,
and every row still passes through the human review editor before anything is
saved.

Parsing helpers are pure functions (unit-tested with mocked pdfplumber output).
"""

import io
import re
from datetime import datetime

import pandas as pd
import pdfplumber

# --- Date patterns -----------------------------------------------------------
# Ambiguous dd.mm.yyyy / mm.dd.yyyy (day-first by default; day>12 heuristic).
_DATE_AMBIG_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b")
# ISO / yyyy.mm.dd / yyyy/mm/dd / yyyy-mm-dd.
_DATE_ISO_RE = re.compile(r"\b(\d{4})[-./](\d{1,2})[-./](\d{1,2})\b")
_DATE_RES = [_DATE_AMBIG_RE, _DATE_ISO_RE]

# --- Amount pattern ----------------------------------------------------------
# Digits with optional '.'/',' groups of 1-3 digits, bounded so it does not
# swallow neighbouring digits (dates are stripped before amounts anyway).
_AMOUNT_RE = re.compile(
    r"(?<![\d.,])([-+]?)(\d+(?:[.,]\d{1,3})*)(?![\d.,])"
)

# Currency symbols stripped before amount parsing.
_CURRENCY_SYMBOLS = "€$£¥₣₹₽₺₴₩₪₫₱₦﷼\u20ac\u20bd\u20a4\u20b9\u20ba"

# --- Column-role vocabulary --------------------------------------------------
_BALANCE_WORDS = re.compile(
    r"balan|saldo|stanje|sold|solda|available|verfügbar|bilan|solde|kontostand|салдо",
    re.IGNORECASE,
)
_DEBIT_WORDS = re.compile(
    r"debit|soll|belast|zadužen|zaduzen|terećen|terecen|duguje|должи|\bout\b|withdrawal",
    re.IGNORECASE,
)
_CREDIT_WORDS = re.compile(
    r"credit|haben|gutschrift|prihod|potraž|potraz|potražuje|potrazuje|побарува|\bin\b|deposit",
    re.IGNORECASE,
)
_DATE_WORDS = re.compile(
    r"\bdate\b|datum|датум|dátum|booked|posting",
    re.IGNORECASE,
)
_AMOUNT_WORDS = re.compile(
    r"\bamount\b|iznos|износ|betrag|montant|importe|summe|promet|промет",
    re.IGNORECASE,
)
_DESC_WORDS = re.compile(
    r"description|opis|опис|details|narrative|transaction|svrha|namena|намена",
    re.IGNORECASE,
)

# Header vocabulary (used to keep header words out of descriptions).
_HEADER_WORDS = {
    "date", "description", "debit", "credit", "amount", "balance",
    "details", "value", "iznos", "opis", "datum",
    # Serbian
    "duguje", "potražuje", "potrazuje", "saldo", "promet", "stanje", "prenos",
    "zaduženje", "zaduzenje", "odobrenje", "naplata", "uplata",
    # Macedonian (Cyrillic)
    "датум", "опис", "износ", "должи", "побарува", "салдо", "промет", "намена",
    # German
    "betrag", "buchungstag", "wertstellung", "verwendungszweck", "soll", "haben",
    "kontostand",
}

# Summary / balance / page-furniture lines to skip when they carry no date.
_NOISE_RE = re.compile(
    r"opening|closing|carried\s+forward|brought\s+forward|b/?\s?f|c/?\s?f|"
    r"\bbalance\b|saldo|stanje|prenos|starting|ending|\btotal\b|subtotal|suma|"
    r"ukupno|totaal|"
    r"page\s+\d|statement\s+period|account\s+number|\biban\b|sort\s+code|"
    r"\bswift\b|\bbic\b|www\.|\btel\b|\bvat\b|tax\s+id",
    re.IGNORECASE,
)

# Table extraction settings: "lines" first, tolerant "text" fallback for
# borderless PDFs.
_TABLE_SETTINGS = [
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": 4,
        "join_tolerance": 4,
        "text_x_tolerance": 3,
        "text_y_tolerance": 3,
    },
]


def _parse_date_token(tok: str):
    """Parse a date token; returns datetime.date or None.

    Supports dd.mm.yyyy / mm.dd.yyyy (day-first by default, with a day>12
    heuristic for ambiguity) and yyyy-mm-dd / yyyy.mm.dd / yyyy/mm/dd.
    """
    if tok is None:
        return None
    s = str(tok).strip()
    # ISO / yyyy-first dates.
    m = _DATE_ISO_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    # Ambiguous dd/mm vs mm/dd.
    m = _DATE_AMBIG_RE.search(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        if a > 12 and b <= 12:
            d, mo = a, b          # first token is the day (dd/mm)
        elif b > 12 and a <= 12:
            mo, d = a, b          # second token is the day (mm/dd)
        else:
            d, mo = a, b          # default: day-first
        try:
            return datetime(y, mo, d).date()
        except ValueError:
            return None
    return None


def _parse_amount_core(raw: str):
    """Convert a sign-less numeric string (digits, '.', ',') to a float.

    Handles integers, Serbian thousands "1.200" (3-digit dot groups), and the
    decimal forms "1.234,56" / "1,234.56" / "1234,56" / "1200.00".
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    has_dot = "." in raw
    has_comma = "," in raw
    if has_dot and has_comma:
        # The last separator is the decimal separator.
        if raw.rfind(".") > raw.rfind(","):
            dec, thousands = ".", ","
        else:
            dec, thousands = ",", "."
        intpart, _, frac = raw.rpartition(dec)
        intpart = intpart.replace(thousands, "")
        frac = frac.replace(thousands, "")
    elif has_dot:
        groups = raw.split(".")
        if all(len(g) == 3 for g in groups[1:]):
            # Thousands separator (Serbian "1.200").
            intpart, frac = "".join(groups), ""
        else:
            intpart, _, frac = raw.rpartition(".")
            intpart = intpart.replace(".", "")
    elif has_comma:
        groups = raw.split(",")
        if all(len(g) == 3 for g in groups[1:]):
            # Thousands separator ("1,200").
            intpart, frac = "".join(groups), ""
        else:
            intpart, _, frac = raw.rpartition(",")
            intpart = intpart.replace(",", "")
    else:
        intpart, frac = raw, ""
    intpart = intpart or "0"
    try:
        if frac:
            return float(f"{intpart}.{frac}")
        return float(intpart)
    except ValueError:
        return None


def _parse_amount_token(tok: str) -> float | None:
    """Parse one amount token to a float, or None.

    Accepts integers ("1200"), Serbian thousands ("1.200"), decimal forms
    ("1.234,56" / "1,234.56"), parenthesised negatives ("(45.00)") and
    currency symbols. Dates are recognised and never parsed as amounts.
    """
    if tok is None:
        return None
    s = str(tok).strip()
    if not s:
        return None
    # Normalise typographic minus and non-breaking spaces.
    s = s.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u00a0", " ").strip()
    sign = 1.0
    # Parenthesised negative: (45.00) -> -45.00.
    if s.startswith("(") and s.endswith(")"):
        sign = -1.0
        s = s[1:-1].strip()
    # Trailing minus (accounting style): 45.00- -> -45.00.
    if s.endswith("-"):
        sign *= -1.0
        s = s[:-1].strip()
    # Strip currency symbols from either end.
    s = s.strip(_CURRENCY_SYMBOLS + " \t")
    m = _AMOUNT_RE.search(s)
    if not m:
        return None
    # A date-like match is not an amount.
    if _parse_date_token(m.group(0)) is not None:
        return None
    if m.group(1) == "-":
        sign *= -1.0
    val = _parse_amount_core(m.group(2))
    if val is None:
        return None
    return sign * val


def _is_noise(s: str) -> bool:
    return bool(_NOISE_RE.search(s or ""))


def _is_monotonic(vals) -> bool:
    if len(vals) < 2:
        return False
    n = len(vals) - 1
    inc = sum(1 for a, b in zip(vals, vals[1:]) if b >= a)
    dec = sum(1 for a, b in zip(vals, vals[1:]) if b <= a)
    return max(inc, dec) >= 0.8 * n


def _classify_columns(rows) -> dict:
    """Classify columns by header words found in the rows before the first
    dated (data) row. Returns {column_index: role} where role is one of
    'date', 'description', 'debit', 'credit', 'amount', 'balance'."""
    if not rows:
        return {}
    rows = [list(r) for r in rows if r]
    if not rows:
        return {}
    n_cols = max(len(r) for r in rows)
    header_rows = []
    for r in rows:
        cells = [str(c or "").strip() for c in r]
        if any(_parse_date_token(c) is not None for c in cells):
            break
        header_rows.append(r)
    roles = {}
    for col in range(n_cols):
        texts = [str(r[col]).strip() for r in header_rows if col < len(r)]
        joined = " ".join(texts)
        if not joined.strip():
            continue
        j = joined.lower()
        if _BALANCE_WORDS.search(j):
            roles[col] = "balance"
        elif _DEBIT_WORDS.search(j):
            roles[col] = "debit"
        elif _CREDIT_WORDS.search(j):
            roles[col] = "credit"
        elif _DATE_WORDS.search(j):
            roles[col] = "date"
        elif _AMOUNT_WORDS.search(j):
            roles[col] = "amount"
        elif _DESC_WORDS.search(j):
            roles[col] = "description"
    return roles


def _detect_balance_column(rows):
    """Headerless heuristic: return the index of the rightmost numeric column
    that appears on nearly every dated row and changes monotonically (a running
    balance), or None."""
    if not rows:
        return None
    rows = [list(r) for r in rows if r]
    if not rows:
        return None
    n_cols = max(len(r) for r in rows)
    data_rows = [
        r for r in rows
        if any(_parse_date_token(str(c or "").strip()) is not None for c in r)
    ]
    if len(data_rows) < 3:
        return None
    col_vals = {}
    for r in data_rows:
        for col in range(n_cols):
            cell = str(r[col] or "").strip() if col < len(r) else ""
            if _parse_date_token(cell) is not None:
                continue
            v = _parse_amount_token(cell)
            if v is not None:
                col_vals.setdefault(col, []).append(v)
    for col in range(n_cols - 1, -1, -1):
        vals = col_vals.get(col, [])
        if not vals:
            continue
        if len(vals) / len(data_rows) < 0.7:
            continue
        if _is_monotonic(vals):
            return col
    return None


def parse_text_lines(text: str) -> list[dict]:
    """Extract transactions from plain text lines (date ... description ... amount)."""
    out = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        d = _parse_date_token(s)
        # Strip dates BEFORE looking for amounts (dates must not parse as amounts).
        s2 = s
        for rx in _DATE_RES:
            s2 = rx.sub(" ", s2)
        amts = [a for a in (_parse_amount_token(t) for t in s2.split()) if a is not None]
        if d is None:
            # Noise lines (summary/balance/page furniture) are never continuations.
            if _is_noise(s):
                continue
            if amts:
                continue  # amount fragment with no date: skip
            # Wrapped description continuation of the previous transaction.
            if out:
                cont = re.sub(r"\s{2,}", " ", s).strip(" -–—|")
                if cont:
                    out[-1]["description"] += " " + cont
            continue
        # Has a date.
        if not amts:
            continue
        # Trailing balance heuristic: with 2+ amounts the last is the running
        # balance, so the transaction amount is the FIRST amount.
        amount = amts[0] if len(amts) >= 2 else amts[-1]
        if amount == 0 or abs(amount) > 1_000_000:
            continue
        desc = _AMOUNT_RE.sub("", s2).strip()
        desc = re.sub(r"\s{2,}", " ", desc).strip(" -–—|")
        if not desc:
            desc = "Bank transaction"
        out.append({"date": d, "description": desc, "amount": amount, "currency": "EUR"})
    return out


def parse_table_rows(rows) -> list[dict]:
    """Extract transactions from raw table rows (lists of cell strings)."""
    out = []
    if not rows:
        return out
    rows = [list(r) for r in rows]
    roles = _classify_columns(rows)
    balance_col = None if "balance" in roles.values() else _detect_balance_column(rows)
    for row in rows:
        if not row:
            continue
        cells = [str(c or "").strip() for c in row]
        d = None
        amounts = []  # (column_index, signed_amount)
        desc_parts = []
        for col, cell in enumerate(cells):
            if not cell:
                continue
            role = roles.get(col)
            cell_date = _parse_date_token(cell)
            if d is None and cell_date is not None:
                d = cell_date
                continue
            # Skip balance cells (by header role or content heuristic).
            if role == "balance" or col == balance_col:
                continue
            if role == "date":
                continue
            a = None if cell_date is not None else _parse_amount_token(cell)
            if a is not None:
                if role == "debit":
                    a = -abs(a)
                elif role == "credit":
                    a = abs(a)
                amounts.append((col, a))
                continue
            # Description candidate.
            if (role in (None, "description")
                    and cell.lower() not in _HEADER_WORDS
                    and not _is_noise(cell)):
                desc_parts.append(cell)
        if d is None or not amounts:
            continue
        # The balance column (if any) is already excluded, so the first remaining
        # amount is the transaction amount — avoids "last amount wins".
        amount = amounts[0][1]
        if amount == 0 or abs(amount) > 1_000_000:
            continue
        desc = " ".join(desc_parts).strip() or "Bank transaction"
        out.append({"date": d, "description": desc, "amount": amount, "currency": "EUR"})
    return out


def _extract_tables(page, settings):
    try:
        return page.extract_tables(settings)
    except TypeError:
        # pdfplumber mocks / older versions without table-settings support.
        return page.extract_tables()
    except Exception:
        return []


def extract_transactions_from_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Open a PDF with pdfplumber and pull transactions from tables or text.

    Returns the normalized DataFrame (date, description, amount, currency).
    """
    all_rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parsed_any = False
            for settings in _TABLE_SETTINGS:
                try:
                    tables = _extract_tables(page, settings)
                except Exception:
                    tables = []
                for t in tables or []:
                    parsed = parse_table_rows(t)
                    if parsed:
                        all_rows.extend(parsed)
                        parsed_any = True
                if parsed_any:
                    break
            if not parsed_any:
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                all_rows.extend(parse_text_lines(text))
    df = pd.DataFrame(all_rows, columns=["date", "description", "amount", "currency"])
    return df.dropna(subset=["date", "amount"])
