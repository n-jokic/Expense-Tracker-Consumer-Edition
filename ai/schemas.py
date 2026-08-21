"""
ai/schemas.py — typed contracts for the advisor (Phase 3 A3).

No finance arithmetic here — just dataclasses for provenance and results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ToolProvenance:
    """Provenance for any finance-tool result."""
    period_start: date | None = None
    period_end: date | None = None
    row_count: int = 0
    filters: dict[str, str] = field(default_factory=dict)
    currency_basis: str = "EUR"
    calculation: str = ""


@dataclass
class FinanceToolResult:
    """One finance tool's typed result with provenance."""
    data: dict[str, Any] = field(default_factory=dict)
    provenance: ToolProvenance = field(default_factory=ToolProvenance)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.data)
        out["_provenance"] = {
            "period": (
                f"{self.provenance.period_start.isoformat()}..{self.provenance.period_end.isoformat()}"
                if self.provenance.period_start and self.provenance.period_end
                else (self.provenance.period_start.isoformat() if self.provenance.period_start else None)
            ),
            "row_count": self.provenance.row_count,
            "filters": self.provenance.filters,
            "currency_basis": self.provenance.currency_basis,
            "calculation": self.provenance.calculation,
        }
        if self.error:
            out["error"] = self.error
        return out
