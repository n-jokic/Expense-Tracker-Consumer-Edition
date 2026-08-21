"""
ui/board.py — GroupedBoard v2 stub (Phase 2 U3).

Extracted card board for recurring/wishlist/dashboard etc. GroupedBoard
does NOT write to DB — it returns a BoardResult; the caller interprets
cross-group moves (e.g. recurring category) or layout changes (dashboard).

The live implementation currently lives in utils.draggable_card_board;
Phase 2 will move it here with the BoardResult / ItemMove / BoardAction
types and the capability matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ItemMove:
    id: str
    group: str
    position: int


@dataclass
class BoardResult:
    group_order: list[str] = field(default_factory=list)
    collapsed_groups: set[str] = field(default_factory=set)
    item_order: dict[str, list[str]] = field(default_factory=dict)
    moved_items: list[ItemMove] = field(default_factory=list)
    action: object | None = None
