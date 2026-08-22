"""ingestion/receipt/date_extractor.py — O6 date extraction."""

from __future__ import annotations

import re
from datetime import date, timedelta
from ingestion.receipt.confidence import normalize_confidences
from ingestion.receipt.models import FieldCandidate, OCRDocument

_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b"),
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2})\b"),
    re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b"),
]

def _parse_candidate(s: str):
    for pat in _DATE_PATTERNS:
        m = pat.search(s)
        if not m:
            continue
        try:
            if pat.pattern.startswith("\\b(\\d{4})"):
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                d, mo, y = m.groups()[:3]
                d, mo, y = int(d), int(mo), int(y)
                if y < 100:
                    y += 2000 if y < 50 else 1900
            dt = date(y, mo, d)
            return dt
        except Exception:
            continue
    return None

def extract_date_candidates(document: OCRDocument, raw_text: str | None = None) -> list[FieldCandidate]:
    if not raw_text:
        if document and document.tokens:
            by_line: dict[int, list[str]] = {}
            for t in document.tokens:
                by_line.setdefault(t.line_id, []).append(t.text)
            raw_text = "\n".join(" ".join(by_line[k]) for k in sorted(by_line))
        else:
            raw_text = ""
    if not raw_text:
        return []
    lines = raw_text.splitlines()
    cands: list[tuple] = []
    today = date.today()
    for idx, line in enumerate(lines):
        dt = _parse_candidate(line)
        if not dt:
            continue
        score = 0.0
        reasons: list[str] = []
        low = line.lower()
        if "datum" in low or "date" in low:
            score += 2; reasons.append("near DATUM/DATE +2")
        # location: upper/middle receipt
        if idx < len(lines) * 0.5:
            score += 1; reasons.append("upper/middle +1")
        # valid range
        if dt > today:
            score -= 5; reasons.append("future date -5")
        elif dt < today - timedelta(days=365*10):
            score -= 3; reasons.append("very old -3")
        else:
            score += 1; reasons.append("valid range +1")
        # proximity to time token
        if re.search(r"\b\d{1,2}:\d{2}\b", line):
            score += 1; reasons.append("time proximity +1")
        cands.append((dt, score, reasons, idx, line))
    if not cands:
        return []
    cands.sort(key=lambda x: x[1], reverse=True)
    scores = [c[1] for c in cands]
    confs = normalize_confidences(scores, ceiling=0.96, single_value=0.75)
    out: list[FieldCandidate] = []
    for (dt, _sc, rs, _idx, _line), conf in zip(cands, confs):
        if dt > today or dt < today - timedelta(days=365*10):
            conf = min(conf, 0.45)
        out.append(FieldCandidate(value=dt, confidence=float(conf), reasons=tuple(rs)))
    return out
