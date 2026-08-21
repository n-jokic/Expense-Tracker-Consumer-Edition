"""
ai/schemas.py — typed contracts for the advisor (Phase 3 A3).

No finance arithmetic here — just dataclasses for provenance and results.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ToolProvenance:
    """Provenance for any finance-tool result.

    Mirrors spec A3: period, row_count, filters, currency basis and the
    calculation name so the UI can render
    "€842.51 vs €617.90 — Based on 49 transactions [Show source]".
    """

    period_start: date | None = None
    period_end: date | None = None
    # For comparisons the previous period is carried separately.
    previous_period_start: date | None = None
    previous_period_end: date | None = None
    row_count: int = 0
    filters: dict[str, str] = field(default_factory=dict)
    currency_basis: str = "EUR"
    calculation: str = ""
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        period = None
        if self.period_start and self.period_end:
            period = f"{self.period_start.isoformat()}..{self.period_end.isoformat()}"
        elif self.period_start:
            period = self.period_start.isoformat()
        previous_period = None
        if self.previous_period_start and self.previous_period_end:
            previous_period = f"{self.previous_period_start.isoformat()}..{self.previous_period_end.isoformat()}"
        return {
            "period": period,
            "previous_period": previous_period,
            "row_count": self.row_count,
            "filters": self.filters,
            "currency_basis": self.currency_basis,
            "calculation": self.calculation,
            "truncated": self.truncated,
        }


@dataclass
class FinanceToolResult:
    """One finance tool's typed result with provenance."""

    data: dict[str, Any] = field(default_factory=dict)
    provenance: ToolProvenance = field(default_factory=ToolProvenance)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.data)
        out["_provenance"] = self.provenance.to_dict()
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class AdvisorToolCall:
    """One executed tool call in an advisor turn."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AdvisorResponse:
    """Result of one advisor turn — rendered by app_pages/ask.py."""

    answer: str | None = None
    tool_calls: list[AdvisorToolCall] = field(default_factory=list)
    error: str | None = None
    diagnostic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "tool_calls": [asdict(c) for c in self.tool_calls],
            "error": self.error,
            "diagnostic": self.diagnostic,
        }
