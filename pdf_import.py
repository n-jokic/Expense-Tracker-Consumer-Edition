"""
pdf_import.py — Bank statement PDF parsing (generic, review-first).

Most consumer banks export statements as PDFs. We extract tables where the
PDF has them, otherwise parse text lines with date/amount patterns. Output is
always the same normalized frame the CSV importer produces, and every row
still passes through the human review editor before anything is saved.

Parsing helpers are pure functions (unit-tested with mocked pdfplumber output).
"""

import io
import re
from datetime import datetime

import pandas as pd
import pdfplumber

_DATE_RES = [
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b"),   # dd.mm.yyyy / dd/mm/yyyy
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),               # yyyy-mm-dd
]
_AMOUNT_RE = re.compile(r"(?<![\d.,])(?:-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|-?\d+\.\d{2}|-?\d+,\d{2})")


def _parse_date_token(tok: str):
    for rx in _DATE_RES:
        m = rx.search(tok)
        if m:
            try:
                if rx is _DATE_RES[0]:
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    y = y + 2000 if y < 100 else y
                else:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return datetime(y, mo, d).date()
            except ValueError:
                continue
    return None


def _parse_amount_token(tok: str) -> float | None:
    m = _AMOUNT_RE.search(tok)
    if not m:
        return None
    raw = m.group()
    sign = -1.0 if raw.startswith("-") else 1.0
    raw = raw.lstrip("-+")
    try:
        last_sep = max(raw.rfind("."), raw.rfind(","))
        if last_sep > 0 and len(raw) - last_sep - 1 == 2:
            intpart = raw[:last_sep].replace(".", "").replace(",", "")
            val = float(f"{intpart}.{raw[last_sep+1:]}")
        else:
            val = float(raw.replace(",", ""))
        return sign * val
    except ValueError:
        return None


def parse_text_lines(text: str) -> list[dict]:
    """Extract transactions from plain text lines (date ... description ... amount)."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        d = _parse_date_token(s)
        if d is None:
            continue
        # strip dates BEFORE looking for amounts (dates must not parse as amounts)
        s2 = s
        for rx in _DATE_RES:
            s2 = rx.sub(" ", s2)
        amts = [a for a in (_parse_amount_token(t) for t in s2.split()) if a is not None]
        if not amts:
            continue
        amount = amts[-1]
        desc = _AMOUNT_RE.sub("", s2).strip()
        desc = re.sub(r"\s{2,}", " ", desc).strip(" -–—|")
        if not desc:
            desc = "Bank transaction"
        out.append({"date": d, "description": desc, "amount": amount, "currency": "EUR"})
    return out


_HEADER_WORDS = {"date", "description", "debit", "credit", "amount", "balance",
                 "details", "value", "iznos", "opis", "datum"}


def parse_table_rows(rows) -> list[dict]:
    """Extract transactions from raw table rows (lists of cell strings)."""
    out = []
    for row in rows:
        if not row:
            continue
        d = None
        amount = None
        desc_parts = []
        for cell in row:
            cell = str(cell or "").strip()
            if not cell:
                continue
            is_date = _parse_date_token(cell) is not None
            if d is None and is_date:
                d = _parse_date_token(cell)
                continue
            a = _parse_amount_token(cell)
            if not is_date and a is not None:
                amount = a  # last amount wins
                continue
            if (not is_date and cell.lower() not in _HEADER_WORDS
                    and not cell.lower().startswith(("page", "statement", "account"))):
                desc_parts.append(cell)
        if d is None or amount is None:
            continue
        desc = " ".join(desc_parts).strip() or "Bank transaction"
        out.append({"date": d, "description": desc, "amount": amount, "currency": "EUR"})
    return out


def extract_transactions_from_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Open a PDF with pdfplumber and pull transactions from tables or text.

    Returns the normalized DataFrame (date, description, amount, currency).
    """
    all_rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables()
            except Exception:
                tables = []
            got = False
            for t in tables:
                parsed = parse_table_rows(t)
                if parsed:
                    all_rows.extend(parsed)
                    got = True
            if not got:
                all_rows.extend(parse_text_lines(page.extract_text() or ""))
    df = pd.DataFrame(all_rows, columns=["date", "description", "amount", "currency"])
    return df.dropna(subset=["date", "amount"])
