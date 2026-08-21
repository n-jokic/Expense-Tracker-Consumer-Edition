"""
ingestion/receipt/models.py — OCR receipt data structures (Phase 5 O1).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OCRToken:
    text: str
    confidence: float
    polygon: tuple[tuple[float, float], ...]
    line_id: int


@dataclass
class OCRDocument:
    tokens: list[OCRToken] = field(default_factory=list)
    width: int = 0
    height: int = 0
    mean_confidence: float = 0.0
    engine: str = ""
    model_version: str = ""
    preprocessing: str = ""


@dataclass(frozen=True)
class FieldCandidate:
    value: object
    confidence: float
    reasons: tuple[str, ...] = ()


@dataclass
class ReceiptResult:
    merchant: FieldCandidate | None = None
    total: FieldCandidate | None = None
    date: FieldCandidate | None = None
    time: FieldCandidate | None = None
    currency: FieldCandidate | None = None
    alternatives: dict[str, list[FieldCandidate]] = field(default_factory=dict)
    document: OCRDocument = field(default_factory=OCRDocument)
