"""Field-level OCR benchmark contract and deterministic metric checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ingestion.receipt.confidence import is_low_confidence, was_wrong_but_high_confidence
from ingestion.receipt.models import FieldCandidate, OCRDocument, ReceiptResult


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "receipts"
CRITICAL_FIELDS = ("merchant", "total", "date", "currency")


@dataclass(frozen=True)
class BenchmarkCase:
    expected: dict
    result: ReceiptResult


def _same_value(field: str, actual, expected) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if field == "merchant":
        return " ".join(str(actual).casefold().split()) == " ".join(str(expected).casefold().split())
    if field == "currency":
        return str(actual).upper() == str(expected).upper()
    if field == "total":
        return abs(float(actual) - float(expected)) <= 0.01
    if field == "date":
        if isinstance(actual, (date,)):
            actual = actual.isoformat()
        return str(actual) == str(expected)
    return actual == expected


def benchmark_metrics(cases: list[BenchmarkCase]) -> dict[str, float | int]:
    field_hits = {field: 0 for field in CRITICAL_FIELDS}
    field_counts = {field: 0 for field in CRITICAL_FIELDS}
    all_critical = 0
    low_confidence_hits = 0
    wrong_high_confidence = 0

    for case in cases:
        result = case.result
        expected = case.expected
        all_hit = True
        for field in CRITICAL_FIELDS:
            if field not in expected:
                continue
            field_counts[field] += 1
            candidate = getattr(result, field)
            actual = candidate.value if candidate else None
            hit = _same_value(field, actual, expected[field])
            field_hits[field] += int(hit)
            all_hit = all_hit and hit
        all_critical += int(all_hit)
        low_confidence_hits += int(is_low_confidence(result) == bool(expected.get("low_confidence", False)))
        wrong_high_confidence += int(was_wrong_but_high_confidence(result, expected))

    total = len(cases)
    metrics: dict[str, float | int] = {
        f"{field}_accuracy": field_hits[field] / field_counts[field] if field_counts[field] else 0.0
        for field in CRITICAL_FIELDS
    }
    metrics.update({
        "all_critical_fields_accuracy": all_critical / total if total else 0.0,
        "low_confidence_detection_accuracy": low_confidence_hits / total if total else 0.0,
        "wrong_high_confidence_count": wrong_high_confidence,
        "cases": total,
    })
    return metrics


def _candidate(value, confidence: float) -> FieldCandidate:
    return FieldCandidate(value=value, confidence=confidence)


def test_fixture_manifest_covers_requested_receipt_conditions():
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert {case["script"] for case in manifest} >= {"serbian-latin", "serbian-cyrillic", "latin"}
    assert {case["currency"] for case in manifest} >= {"EUR", "RSD"}
    features = {feature for case in manifest for feature in case["features"]}
    assert {"cash", "change", "subtotal", "discount", "vat"} <= features
    # OCR-01: the corpus is REAL now — every manifest image is committed and
    # carries line-item expectations plus a reconciliation verdict.
    assert all((FIXTURE_DIR / case["image"]).exists() for case in manifest)
    for case in manifest:
        assert isinstance(case.get("items"), list) and case["items"], case["id"]
        assert isinstance(case.get("reconciles"), bool), case["id"]


def test_benchmark_reports_field_accuracy_and_uncertainty_metrics():
    good = ReceiptResult(
        merchant=_candidate("Lidl", 0.96),
        total=_candidate(2340.50, 0.97),
        date=_candidate("2026-08-17", 0.93),
        currency=_candidate("RSD", 0.99),
    )
    uncertain = ReceiptResult(
        merchant=_candidate("Market ABC", 0.90),
        total=_candidate(2340.0, 0.54),
        date=_candidate("2026-08-20", 0.92),
        currency=_candidate("RSD", 0.96),
        alternatives={"total": [_candidate(2660.0, 0.51)]},
    )
    wrong = ReceiptResult(
        merchant=_candidate("Lidl", 0.91),
        total=_candidate(5000.0, 0.92),
        date=_candidate("2026-08-19", 0.91),
        currency=_candidate("RSD", 0.98),
    )
    cases = [
        BenchmarkCase({"merchant": "Lidl", "total": 2340.50, "date": "2026-08-17", "currency": "RSD"}, good),
        BenchmarkCase({"merchant": "Market ABC", "total": 2660.0, "date": "2026-08-20", "currency": "RSD", "low_confidence": True}, uncertain),
        BenchmarkCase({"merchant": "Maxi", "total": 5000.0, "date": "2026-08-19", "currency": "RSD"}, wrong),
    ]

    metrics = benchmark_metrics(cases)

    assert metrics["merchant_accuracy"] == 2 / 3
    assert metrics["total_accuracy"] == 2 / 3
    assert metrics["currency_accuracy"] == 1.0
    assert metrics["all_critical_fields_accuracy"] == 1 / 3
    assert metrics["low_confidence_detection_accuracy"] == 1.0
    assert metrics["wrong_high_confidence_count"] == 1


def test_benchmark_uses_real_ocr_document_shape():
    result = ReceiptResult(
        merchant=_candidate("Lidl", 0.96),
        total=_candidate(2340.5, 0.97),
        currency=_candidate("RSD", 0.99),
        document=OCRDocument(mean_confidence=0.95, engine="test"),
    )
    assert result.document.engine == "test"
    assert benchmark_metrics([BenchmarkCase({"merchant": "Lidl", "total": 2340.5, "currency": "RSD"}, result)])[
        "all_critical_fields_accuracy"
    ] == 1.0
