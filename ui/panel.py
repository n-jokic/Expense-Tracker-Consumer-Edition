"""
ui/panel.py — Panel primitive stub (Phase 2 U1).

Re-usable panel shell: header, collapse/expand, summary/badge, actions slot,
drag handle when enabled, stable ID, layout-state integration. Content is
rendered by the caller (shell only).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PanelSpec:
    id: str
    title: str
    icon: str | None = None
    collapsible: bool = True
    default_expanded: bool = True
    reorderable: bool = False
