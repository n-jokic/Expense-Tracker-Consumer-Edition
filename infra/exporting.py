"""infra/exporting.py — Excel export (+ formula-injection hardening)."""

from __future__ import annotations

import io

import pandas as pd

_XL_UNSAFE_PREFIXES = ("=", "+", "@")

def _xl_safe(v):
    if isinstance(v, str) and v.startswith(_XL_UNSAFE_PREFIXES):
        return "'" + v
    if isinstance(v, str) and len(v) > 1 and v[0] == "-" and v[1].isdigit():
        return "'" + v
    return v

def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    safe = df.copy()
    from pandas.api import types as pd_types
    for col in safe.columns:
        if pd_types.is_string_dtype(safe[col]) or safe[col].dtype == object:
            safe[col] = safe[col].astype(object).map(_xl_safe)
    safe.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()
