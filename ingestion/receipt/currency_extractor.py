"""ingestion/receipt/currency_extractor.py — O7 currency extraction."""

from __future__ import annotations

import re
from ingestion.receipt.models import FieldCandidate, OCRDocument

# Boundary-safe: \b can never sit against non-word chars like €/$, so anchor
# with lookarounds instead of word boundaries.
_CURRENCY_PAT = re.compile(r"(?<![A-Za-z])(?:rsd|din\.?|eur|usd|chf|gbp|hrk|bam|huf|ron|bgn|pln|czk|€|\$)(?![A-Za-z])", re.IGNORECASE)
_CURRENCY_MAP = {"din": "RSD", "din.": "RSD", "€": "EUR", "$": "USD"}
_LOCALE_CURRENCY = {
    "de": "EUR", "fr": "EUR", "es": "EUR", "it": "EUR", "nl": "EUR",
    "at": "EUR", "be": "EUR", "pt": "EUR", "ie": "EUR", "fi": "EUR",
    "gr": "EUR", "sk": "EUR", "si": "EUR", "hr": "EUR",
    "en-us": "USD", "en-gb": "GBP",
}


def _locale_to_currency(user_locale: str | None, default_currency: str) -> str:
    """Case-insensitive BCP-47-ish locale tag -> fallback currency."""
    fb = str(default_currency or "EUR").upper()
    if not user_locale:
        return fb
    norm = str(user_locale).strip().lower().replace("_", "-")
    lang = norm.split("-")[0]
    mapped = _LOCALE_CURRENCY.get(norm) or _LOCALE_CURRENCY.get(lang)
    return mapped or fb


def extract_currency_candidates(document: OCRDocument, raw_text: str | None = None, user_locale: str | None = None, default_currency: str = "EUR") -> list[FieldCandidate]:
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
    if not cands:
        # Nothing in the text looked like a currency -> ONE low-confidence
        # fallback: user's locale currency, else default_currency.
        fb_cur = _locale_to_currency(user_locale, default_currency)
        cands.append((fb_cur, 0.3, ("locale/default-currency fallback +0.3",)))
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
