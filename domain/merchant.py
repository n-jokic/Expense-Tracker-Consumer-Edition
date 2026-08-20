"""
domain/merchant.py — merchant normalization (Streamlit-free).

Deterministic cleaning shared by bank import, OCR, categorization,
recurring detection, anomaly detection, LLM merchant search, and dashboard
analytics.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_BANK_PREFIXES = re.compile(r"^(?:pos|purchase|payment|debit|c\/o|terminal)\b[\s:]*", re.I)
_MASKED_CARD = re.compile(r"(?:\*+\s*\d{2,4}|x+\d{2,4})", re.I)
_STORE_ID = re.compile(r"(?:\s+|^)(?:#\s*\d+|no\.?\s*\d+|store\s*\d+|prod\s*\d+)(?:\s+|$)", re.I)
_TX_ID = re.compile(r"\b[a-z]{0,2}\d{6,}\b", re.I)
_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")

# Suffix-ish tokens seen in Serbian receipts that are not the merchant core.
_SUFFIX_TOKENS = re.compile(
    r"\b(?:doo|d\.o\.o\.?|ad|a\.d\.?|beograd|bg|novisad|srbija|srbi?a)\b", re.I
)

# Small built-in alias table (deterministic, no DB yet per R5 spec).
_ALIAS_NORM_TO_CANONICAL: dict[str, str] = {
    "lidl": "Lidl",
    "kaufland": "Kaufland",
    "maxi": "Maxi",
    "idea": "Idea",
    "tempo": "Tempo",
    "dis": "DIS",
    "mcdonalds": "McDonald's",
    "kfc": "KFC",
}


@dataclass(frozen=True)
class MerchantMatch:
    raw: str
    normalized: str
    canonical: str | None
    confidence: float
    source: str


def _strip_suffix_words(s: str) -> str:
    # Remove isolated suffix words, but keep the core token.
    return _SUFFIX_TOKENS.sub(" ", s).strip()


def _seeded_aliases(known_canonicals: list[str] | None = None) -> dict[str, str]:
    """Combine built-in aliases with casefolded known_canonicals."""
    out = dict(_ALIAS_NORM_TO_CANONICAL)
    if known_canonicals:
        for c in known_canonicals:
            out.setdefault(c.casefold(), c)
    return out


def normalize_merchant(raw: str) -> str:
    """Deterministic cleaning: unicode normalize, casefold, strip IDs/prefixes/punct.

    Examples:
      LIDL SRBIJA DOO #0183 BEOGRAD → lidl
      LIDL-183 → lidl
      LIDL PROD 0183 → lidl
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKD", raw)
    # remove combining marks so "SRBIJA" with accents still matches
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = _BANK_PREFIXES.sub("", s)
    s = _MASKED_CARD.sub(" ", s)
    s = _TX_ID.sub(" ", s)
    s = _STORE_ID.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = _strip_suffix_words(s)
    s = _WS.sub(" ", s).strip()
    # Collapse to single token when the core is obvious (first alpha token)
    # but keep as-is when multiple meaningful tokens remain (e.g. "burger king").
    # For the Lidl examples we want "lidl" alone.
    if s and " " in s:
        toks = [t for t in s.split() if t]
        # If alias in tokens, prefer it alone
        for t in toks:
            if t in _ALIAS_NORM_TO_CANONICAL:
                return t
    return s.strip()


def match_merchant(raw: str, known_canonicals: list[str] | None = None) -> MerchantMatch:
    """Map raw merchant text to normalized + optional canonical.

    If ``known_canonicals`` is provided, try case-insensitive alias lookup
    on the normalized form.
    """
    normalized = normalize_merchant(raw)
    canonical: str | None = None
    confidence = 0.0
    source = "normalized"
    if not normalized:
        return MerchantMatch(raw=raw or "", normalized="", canonical=None,
                             confidence=0.0, source="none")
    # Alias table
    if normalized in _ALIAS_NORM_TO_CANONICAL:
        canonical = _ALIAS_NORM_TO_CANONICAL[normalized]
        confidence = 0.90
        source = "alias"
    elif known_canonicals:
        lookup = {c.casefold(): c for c in known_canonicals}
        if normalized in lookup:
            canonical = lookup[normalized]
            confidence = 0.85
            source = "known_canonicals"
        else:
            # token overlap: try first token
            first = normalized.split()[0] if " " in normalized else normalized
            if first in lookup:
                canonical = lookup[first]
                confidence = 0.60
                source = "token_match"
            else:
                confidence = 0.40
                source = "normalized"
        if canonical is None:
            confidence = 0.40
    else:
        if " " in normalized:
            confidence = 0.50
        else:
            confidence = 0.70
        # single token without alias is still a useful normalization
        if len(normalized) >= 3:
            confidence = max(confidence, 0.60)
    return MerchantMatch(raw=raw, normalized=normalized, canonical=canonical,
                         confidence=confidence, source=source)
