"""
ingestion/receipt/total_extractor.py — O4 Total extraction with candidate scoring.

Never uses "max(amounts)" as fallback alone; largest amount is +1 weak feature at most.
"""

from __future__ import annotations

import re
from ingestion.receipt.models import FieldCandidate, OCRDocument

_TOTAL_KEYS = ("ukupno", "total", "suma", "svega", "za uplatu", "grand total", "amount due", "to pay", "platiti", "ukupno za uplatu")
_SUBTOTAL_KEYS = ("subtotal", "medjuzbir", "međuzbir", "meduzbir")
_TAX_KEYS = ("pdv", "vat", "tax")
_CASH_KEYS = ("gotovina", "cash")
_CHANGE_KEYS = ("kusur", "change", "povrat", "rest", "vraceno")

_AMT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+[.,]\d{2}")
_CURRENCY_RE = re.compile(r"\b(?:rsd|din\.?|eur|€|usd|\$)\b", re.I)
# Dates must never be parsed as amount phantoms (mirrors ocr.extract_amounts which
# strips them via _DATE_RE.sub before scanning). Kept in sync with ocr._DATE_RE.
_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")

def _amounts_with_positions(text: str) -> list[tuple[float, int, str]]:
    """Return (amount, line_idx, line_text).

    Date patterns are stripped before _AMT_RE scanning (mirrors ocr.extract_amounts)
    so receipt dates like '31.12.2024' never leak phantom amounts (e.g. 31.12) into
    the candidate pool. The original line text is preserved for keyword scoring.
    """
    from ocr import extract_amounts as _extract_amounts  # reuse existing parser for value
    out = []
    lines = text.splitlines() if text else []
    for idx, line in enumerate(lines):
        clean = line.strip()
        if not clean:
            continue
        # Strip dates first so they cannot be parsed as amount phantoms.
        scanned = _DATE_RE.sub(" ", clean)
        # extract via regex then parse
        for m in _AMT_RE.finditer(scanned):
            raw = m.group()
            # Use ocr's extract path would be per-token; approximate via simple parse
            try:
                from pdf_import import _parse_amount_core
                val = _parse_amount_core(raw)
                if val is not None and 0.01 <= val <= 1_000_000:
                    out.append((float(val), idx, clean))
            except Exception:
                continue
    return out


def extract_total_candidates(document: OCRDocument, raw_text: str | None = None) -> list[FieldCandidate]:
    """Score every amount token per O4 weights -> sorted FieldCandidates."""
    # Build text from OCRDocument tokens if raw_text not provided
    if not raw_text:
        if document and document.tokens:
            # group by line_id
            by_line: dict[int, list[str]] = {}
            for t in document.tokens:
                by_line.setdefault(t.line_id, []).append(t.text)
            lines = [" ".join(by_line[k]) for k in sorted(by_line)]
            raw_text = "\n".join(lines)
        else:
            raw_text = ""
    if not raw_text:
        return []
    lines = raw_text.splitlines()
    n_lines = max(len(lines), 1)
    # map amount -> line info
    cands = _amounts_with_positions(raw_text)
    if not cands:
        return []
    # token confidences by line (approx)
    line_conf: dict[int, float] = {}
    if document and document.tokens:
        by_line_conf: dict[int, list[float]] = {}
        for t in document.tokens:
            by_line_conf.setdefault(t.line_id, []).append(t.confidence)
        for lid, confs in by_line_conf.items():
            # lid may not equal line idx when we rebuilt; approximate
            line_conf[lid] = sum(confs) / max(len(confs), 1)
    # Score each amount.
    # `penalized` flags a candidate whose line carries cash/change/tax/subtotal/
    # item-quantity markers -- such amounts are unlikely to be the receipt total
    # and must not receive the largest-amount or adjacency heuristic boosts.
    # `is_total_line` flags a candidate whose line carries a total keyword.
    # `has_total_line` is precomputed: when a total-keyword line exists the
    # adjacency/largest heuristic boosts are reserved for the total-keyword
    # candidate itself (an unpenalized item above the total must not outrank the
    # real, possibly taxed, total on the total line).
    scored: list[tuple[float, float, list[str], int, str, int]] = []  # (amount, score, reasons, line_idx, line, tie)
    has_total_line = any(
        any(k in line.lower() for k in _TOTAL_KEYS) for _, _, line in cands
    )
    max_amt = max(a for a, *_ in cands) if cands else 0
    for amount, line_idx, line in cands:
        score = 0.0
        reasons: list[str] = []
        low = line.lower()
        is_total_line = any(k in low for k in _TOTAL_KEYS)
        penalized = False
        # keyword scores
        if is_total_line:
            score += 5; reasons.append("same line as UKUPNO/TOTAL/ZA UPLATU +5")
        elif has_total_line:
            # A total-keyword line already exists: don't boost non-total-line
            # neighbours (adjacency) - they would let an unpenalized item on the
            # line above outrank the real total. Compete on vertical/currency/ocr.
            reasons.append("adjacency +2 suppressed (total keyword line exists)")
        else:
            if line_idx > 0 and any(k in lines[line_idx-1].lower() for k in _TOTAL_KEYS):
                score += 2; reasons.append("adjacent to total keyword +2")
            elif line_idx < len(lines)-1 and any(k in lines[line_idx+1].lower() for k in _TOTAL_KEYS):
                score += 2; reasons.append("adjacent to total keyword +2")
        # vertical position: bottom 35%
        if line_idx >= n_lines * 0.65:
            score += 2; reasons.append("bottom 35% +2")
        # currency adjacent
        if _CURRENCY_RE.search(line):
            score += 1; reasons.append("currency adjacent +1")
        # OCR conf
        conf = line_conf.get(line_idx, 0)
        if conf and conf > 0.90:
            score += 1; reasons.append(f"OCR conf {conf:.2f} +1")
        elif conf and conf > 0.75:
            score += 0.5; reasons.append(f"OCR conf {conf:.2f} +0.5")
        # penalties
        if any(k in low for k in _SUBTOTAL_KEYS):
            score -= 4; reasons.append("SUBTOTAL -4"); penalized = True
        if any(k in low for k in _TAX_KEYS):
            score -= 4; reasons.append("PDV/VAT/TAX -4"); penalized = True
        if any(k in low for k in _CASH_KEYS):
            score -= 3; reasons.append("CASH/GOTOVINA -3"); penalized = True
        if any(k in low for k in _CHANGE_KEYS):
            score -= 5; reasons.append("KUSUR/CHANGE -5"); penalized = True
        # item row penalty: quantity x unit price pattern
        if re.search(r"\bx\s+\d+[.,]\d{2}\b", low) or re.search(r"\d+\s*[x×]\s*\d", low):
            score -= 2; reasons.append("item quantity×price -2"); penalized = True
        # weak largest-amount feature: +1 only for an unpenalized candidate, and
        # only when it is the max. A penalized cash/change/tax/subtotal amount or
        # a non-total-line item must not be rescued by the largest-amount tie-break.
        if abs(amount - max_amt) < 0.01 and not penalized:
            if is_total_line or not has_total_line:
                score += 1; reasons.append("largest amount +1 (weak)")
            else:
                reasons.append("largest +1 suppressed (non-total line, total exists)")
        # deterministic tiebreak priority (higher wins ties): total-line first,
        # then unpenalized, then earlier line.
        tie = (int(is_total_line), int(not penalized), -line_idx)
        scored.append((amount, score, reasons, line_idx, line, tie))
    # Sort descending score, breaking ties toward the total-keyword, unpenalized
    # candidate (defence-in-depth for equal-net-score situations).
    scored.sort(key=lambda x: (x[1], x[5][0], x[5][1], x[5][2]), reverse=True)
    # Normalize to confidence 0..1 via sigmoid-like scaling
    max_sc = scored[0][1] if scored else 0
    min_sc = scored[-1][1] if scored else 0
    span = max(max_sc - min_sc, 1.0)
    cands_out: list[FieldCandidate] = []
    for amt, sc, rs, li, ln, _tie in scored:
        conf = (sc - min_sc) / span  # 0..1
        # clamp to 0.1..0.95
        conf = max(0.1, min(0.95, 0.2 + conf * 0.75))
        cands_out.append(FieldCandidate(value=float(amt), confidence=float(conf), reasons=tuple(rs)))
    return cands_out
