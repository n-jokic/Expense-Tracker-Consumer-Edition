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

_AMT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+[.,]\d{2}")
_CURRENCY_RE = re.compile(r"\b(?:rsd|din\.?|eur|€|usd|\$)\b", re.I)

def _amounts_with_positions(text: str) -> list[tuple[float, int, str]]:
    """Return (amount, line_idx, line_text)."""
    from ocr import extract_amounts as _extract_amounts  # reuse existing parser for value
    out = []
    lines = text.splitlines() if text else []
    for idx, line in enumerate(lines):
        clean = line.strip()
        if not clean:
            continue
        # extract via regex then parse
        for m in _AMT_RE.finditer(clean):
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
    # Score each amount
    scored: list[tuple[float, float, list[str], int, str]] = []  # (amount, score, reasons, line_idx, line)
    for amount, line_idx, line in cands:
        score = 0.0
        reasons: list[str] = []
        low = line.lower()
        # keyword scores
        if any(k in low for k in _TOTAL_KEYS):
            score += 5; reasons.append("same line as UKUPNO/TOTAL/ZA UPLATU +5")
        elif line_idx > 0 and any(k in lines[line_idx-1].lower() for k in _TOTAL_KEYS):
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
            score -= 4; reasons.append("SUBTOTAL -4")
        if any(k in low for k in _TAX_KEYS):
            score -= 4; reasons.append("PDV/VAT/TAX -4")
        if any(k in low for k in _CASH_KEYS):
            score -= 3; reasons.append("CASH/GOTOVINA -3")
        if any(k in low for k in _CHANGE_KEYS):
            score -= 5; reasons.append("KUSUR/CHANGE -5")
        # item row penalty: quantity x unit price pattern
        if re.search(r"\bx\s+\d+[.,]\d{2}\b", low) or re.search(r"\d+\s*[x×]\s*\d", low):
            score -= 2; reasons.append("item quantity×price -2")
        # weak largest-amount feature: only +1 if this is the max
        # compute later
        scored.append((amount, score, reasons, line_idx, line))
    # add weak largest feature
    max_amt = max(a for a, *_ in scored) if scored else 0
    for idx in range(len(scored)):
        amt, sc, rs, li, ln = scored[idx]
        if abs(amt - max_amt) < 0.01:
            scored[idx] = (amt, sc + 1, rs + ["largest amount +1 (weak)"], li, ln)
    # Sort descending score
    scored.sort(key=lambda x: x[1], reverse=True)
    # Normalize to confidence 0..1 via sigmoid-like scaling
    max_sc = scored[0][1] if scored else 0
    min_sc = scored[-1][1] if scored else 0
    span = max(max_sc - min_sc, 1.0)
    cands_out: list[FieldCandidate] = []
    for amt, sc, rs, li, ln in scored:
        conf = (sc - min_sc) / span  # 0..1
        # clamp to 0.1..0.95
        conf = max(0.1, min(0.95, 0.2 + conf * 0.75))
        cands_out.append(FieldCandidate(value=float(amt), confidence=float(conf), reasons=tuple(rs)))
    return cands_out
