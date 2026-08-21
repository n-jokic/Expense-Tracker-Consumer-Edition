"""
ingestion/receipt/merchant_extractor.py — O5 merchant candidate scoring.
"""

from __future__ import annotations

import re
from ingestion.receipt.models import FieldCandidate, OCRDocument

_ADDRESS_TERMS = ("ul.", "ulica", "street", "beograd", "novisad", "srbija", "adresa")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-]{6,}\d")
_PIB_TERMS = ("pib",)
_MB_TERMS = ("maticni broj", "mb ")
_CASHIER_TERMS = ("kasir", "cashier", "kasa ")
_RECEIPT_TERMS = ("racun", "rn ", "br. racuna")
_AMOUNT_RE = re.compile(r"\d+[.,]\d{2}")

def extract_merchant_candidates(document: OCRDocument, raw_text: str | None = None) -> list[FieldCandidate]:
    if not raw_text:
        if document and document.tokens:
            by_line: dict[int, list[str]] = {}
            for t in document.tokens:
                by_line.setdefault(t.line_id, []).append(t.text)
            lines = [" ".join(by_line[k]) for k in sorted(by_line)]
            raw_text = "\n".join(lines)
        else:
            raw_text = ""
    if not raw_text:
        return []
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if not lines:
        return []
    n = len(lines)
    # height proxy: if OCRDocument has tokens with polygons, estimate relative height
    # fallback: assume first lines are larger
    cands: list[tuple[str, float, list[str]]] = []
    for idx, line in enumerate(lines):
        # Only upper 30-40% candidates, but keep all with strong alias match
        in_upper = idx < max(3, int(n * 0.4))
        # Quick alias check
        try:
            from domain.merchant import normalize_merchant
            norm = normalize_merchant(line)
            in_alias = norm in ("lidl", "kaufland", "maxi", "idea", "tempo", "dis", "mcdonalds", "kfc")
        except Exception:
            in_alias = False
        if not in_upper and not in_alias:
            continue
        score = 0.0
        reasons: list[str] = []
        low = line.lower()
        if in_alias:
            score += 5; reasons.append("known merchant match +5")
        if idx == 0:
            score += 3; reasons.append("top 15% +3")
        elif idx < max(2, int(n * 0.15)):
            score += 1; reasons.append("upper lines +1")
        if 20 <= len(line) <= 50 and line.replace(" ", "").isalpha():
            score += 2; reasons.append("20-50 alpha chars +2")
        # penalties
        if any(t in low for t in _ADDRESS_TERMS):
            score -= 2; reasons.append("address term -2")
        if _PHONE_RE.search(line):
            score -= 3; reasons.append("phone -3")
        if any(t in low for t in _PIB_TERMS):
            score -= 4; reasons.append("PIB -4")
        if any(t in low for t in _MB_TERMS):
            score -= 4; reasons.append("MB -4")
        if any(t in low for t in _CASHIER_TERMS):
            score -= 3; reasons.append("cashier -3")
        if any(t in low for t in _RECEIPT_TERMS):
            score -= 3; reasons.append("receipt number -3")
        if _AMOUNT_RE.search(line):
            score -= 5; reasons.append("amount -5")
        if score <= -2:
            continue
        # Canonical via merchant.py
        try:
            from domain.merchant import match_merchant
            m = match_merchant(line)
            canon = m.canonical or line.strip()
            # boost confidence by alias match confidence
            if m.canonical:
                score += m.confidence * 2
                reasons.append(f"merchant normalize {m.source} conf {m.confidence:.2f}")
        except Exception:
            canon = line.strip()
        cands.append((canon, score, reasons))
    if not cands:
        return []
    cands.sort(key=lambda x: x[1], reverse=True)
    max_sc = cands[0][1]
    min_sc = cands[-1][1]
    span = max(max_sc - min_sc, 1.0)
    out: list[FieldCandidate] = []
    for text, sc, rs in cands:
        conf = (sc - min_sc) / span
        conf = max(0.1, min(0.95, 0.2 + conf * 0.75))
        out.append(FieldCandidate(value=text, confidence=float(conf), reasons=tuple(rs)))
    return out
