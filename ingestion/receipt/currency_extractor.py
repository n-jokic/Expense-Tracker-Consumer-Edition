"""ingestion/receipt/currency_extractor.py — O7 currency extraction."""

from __future__ import annotations

import re
from ingestion.receipt.models import FieldCandidate, OCRDocument

_CURRENCY_PAT = re.compile(r"\b(?:rsd|din\.?|eur|€|usd|\$|chf|gbp|hrk|bam|huf|ron|bgn|pln|czk)\b", re.I)
_CURRENCY_MAP = {"din": "RSD", "din.": "RSD", "€": "EUR", "$": "USD"}

def extract_currency_candidates(document: OCRDocument, raw_text: str | None = None, user_locale: str | None = None) -> list[FieldCandidate]:
    if not raw_text:
        if document and document.tokens:
            by_line: dict[int, list[str]] = {}
            for t in document.tokens:
                by_line.setdefault(t.line_id, []).append(t.text)
            raw_text = " ".join(" ".join(by_line[k]) for k in sorted(by_line))
        else:
            raw_text = ""
    cands: list[tuple] = []
    if raw_text:
        for m in _CURRENCY_PAT.finditer(raw_text):
            raw = m.group().strip()
            key = raw.lower()
            canon = _CURRENCY_MAP.get(key, key.upper().replace(".", ""))
            if canon == "DIN":
                canon = "RSD"
            score = 2.0
            reasons = [f"found '{raw}' +2"]
            cands.append((canon, score, tuple(reasons)))
    if not cands and user_locale:
        # low-confidence fallback
        cands.append((user_locale.upper(), 0.3, ("locale fallback +0.3",)))
    if not cands:
        return []
    # deduplicate by currency, keep highest score
    best: dict[str, tuple[float, tuple]] = {}
    for cur, sc, rs in cands:
        if cur not in best or sc > best[cur][0]:
            best[cur] = (sc, rs)
    out: list[FieldCandidate] = []
    for cur, (sc, rs) in best.items():
        conf = 0.9 if sc >= 2 else 0.3
        out.append(FieldCandidate(value=cur, confidence=float(conf), reasons=rs))
    out.sort(key=lambda x: x.confidence, reverse=True)
    return out
