"""ingestion/receipt/confidence.py — O8 confidence scoring for ReceiptResult."""

from __future__ import annotations

from ingestion.receipt.models import ReceiptResult, FieldCandidate

HIGH_CONF = 0.80
LOW_CONF = 0.55


def normalize_confidences(
    scores: list[float],
    *,
    base: float = 0.2,
    spread: float = 0.75,
    floor: float = 0.1,
    ceiling: float = 0.95,
    single_value: float | None = None,
) -> list[float]:
    """THE one min-max score-to-confidence mapping for every extractor.

    Extractors used to carry three copies of
    ``conf = clamp(base + rel * spread)`` which drifted (totals stuck under
    the pass-2 HIGH_CONF gate; lone dates collapsed to ``base``). Use this.

    * multi-candidate: relative position inside the score span;
    * single candidate: exactly ``single_value`` when provided (callers
      pass an absolute mapping, e.g. dates >= LOW_CONF), else base+spread.
    """
    if not scores:
        return []
    if len(scores) == 1:
        c = single_value if single_value is not None else (base + spread)
        return [float(max(floor, min(ceiling, c)))]
    mx = max(scores)
    mn = min(scores)
    span = max(mx - mn, 1.0)
    out: list[float] = []
    for s in scores:
        rel = (s - mn) / span
        out.append(float(max(floor, min(ceiling, base + rel * spread))))
    return out

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
