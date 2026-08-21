"""
ml/registry.py — ML registry (Phase 4 M1 stub).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ModelInfo:
    name: str
    version: int
    trained_rows: int
    trained_at: datetime
    dataset_fingerprint: str
    metrics: dict[str, float]
