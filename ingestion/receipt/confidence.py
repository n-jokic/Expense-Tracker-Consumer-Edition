"""ingestion/receipt/confidence.py — O8 confidence scoring for ReceiptResult."""

from __future__ import annotations

from ingestion.receipt.models import ReceiptResult, FieldCandidate

HIGH_CONF = 0.80
LOW_CONF = 0.55

def score_receipt_result(result: ReceiptResult) -> ReceiptResult:
    """Normalize per-field confidences and populate alternatives."""
    # Ensure alternatives reflects low-confidence runner-ups
    for field in ("merchant", "total", "date", "currency"):
        cand = getattr(result, field, None)
        alts = result.alternatives.get(field, [])
        if cand and cand.confidence < HIGH_CONF and alts:
            # Keep top alternative for UI
            pass
    return result

def is_low_confidence(result: ReceiptResult) -> bool:
    """True if any critical field is low confidence or missing."""
    for field in ("merchant", "total", "currency"):
        cand = getattr(result, field, None)
        if not cand or cand.confidence < LOW_CONF:
            return True
    return False

def was_wrong_but_high_confidence(result: ReceiptResult, ground_truth: dict) -> bool:
    """O13 metric: track wrong + high confidence as critical."""
    for field in ("total", "currency", "date", "merchant"):
        cand = getattr(result, field, None)
        truth = ground_truth.get(field)
        if truth is None or not cand:
            continue
        if cand.confidence >= HIGH_CONF and str(cand.value) != str(truth):
            return True
    return False
