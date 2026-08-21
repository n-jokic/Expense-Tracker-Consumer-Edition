"""
ui/layout_state.py — persistent layout state (Phase 2 U2 stub).

Schema:
  {"version": 1, "dashboard": {"order": [...], "collapsed": [...]}}

Layout state != domain state: panel positions live in user-settings JSON,
not on a budget/savings row. Either reuse the existing user_settings JSON
facility or add a dedicated preference table if settings are too flat.
"""

from __future__ import annotations

DEFAULT_LAYOUT = {"version": 1, "dashboard": {"order": [], "collapsed": []}}
