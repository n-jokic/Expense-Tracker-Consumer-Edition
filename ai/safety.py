"""
ai/safety.py — advisor safety boundary (Phase 3 A7).

READ ONLY initially. No direct SQL. No direct service mutation.
Proposed mutations must be confirmed via UI confirmation button that calls
the command service directly — the model never executes them.
"""

from __future__ import annotations

# No-op for scaffold — real policy lives in orchestrator/router.
# Keep file so A7 check (no SQL / no mutation) has a place to audit.
