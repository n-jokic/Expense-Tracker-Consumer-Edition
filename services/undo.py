"""
services/undo.py — reversible command framework (#26 E1).

A mutation command may return a CommandResultWithUndo carrying an UndoToken.
Tokens live in a small in-process registry (single-user local app): the ask
page reads the newest tokens for the user to render Undo cards; execute_undo
dispatches to the inverse command registered in services.commands.

Idempotency contract: every inverse command tolerates being applied twice
(already-deleted -> ok no-op, already-restored -> ok no-op).

Known limitation (documented in plan.md and in-app): gamification side
effects (milestones, fun-money bonuses) are NOT clawed back by an undo —
they re-evaluate naturally on the next load.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

log = logging.getLogger("services.undo")

UNDO_TTL_DAYS = 30
_MAX_TRACKED = 200          # bound memory; oldest tokens drop first


@dataclass(frozen=True)
class UndoToken:
    token_id: str
    inverse_command: str            # key in services.commands.UNDO_COMMANDS
    inverse_args: dict = field(default_factory=dict)
    description: str = ""
    expires_at: datetime | None = None

    def expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.utcnow()
        return now >= self.expires_at


def make_undo_token(inverse_command: str, inverse_args: dict | None,
                    description: str = "") -> UndoToken:
    """Build a token that expires in UNDO_TTL_DAYS days."""
    return UndoToken(
        token_id=str(uuid.uuid4()),
        inverse_command=str(inverse_command),
        inverse_args=dict(inverse_args or {}),
        description=str(description or ""),
        expires_at=datetime.utcnow() + timedelta(days=UNDO_TTL_DAYS),
    )


# token_id -> UndoToken. Module-level on purpose: survives Streamlit reruns
# within one process; a restart simply forfeits undo offers (safe direction).
_REGISTRY: dict[str, UndoToken] = {}
_ORDER: list[str] = []


def register_token(token: UndoToken) -> UndoToken:
    # prune expired first so the cap applies to live offers only
    for tid in [t for t in _ORDER]:
        tok = _REGISTRY.get(tid)
        if tok is not None and tok.expired():
            _REGISTRY.pop(tid, None)
            _ORDER.remove(tid)
    _REGISTRY[token.token_id] = token
    _ORDER.append(token.token_id)
    while len(_ORDER) > _MAX_TRACKED:
        old = _ORDER.pop(0)
        _REGISTRY.pop(old, None)
    return token


def get_token(token_id: str) -> UndoToken | None:
    tok = _REGISTRY.get(str(token_id))
    if tok is None:
        return None
    if tok.expired():
        _REGISTRY.pop(tok.token_id, None)
        try:
            _ORDER.remove(tok.token_id)
        except ValueError:
            pass
        return None
    return tok


def latest_tokens_for_user(user_id: int, limit: int = 3) -> list[UndoToken]:
    """Newest-first live tokens belonging to this user's recent mutations.

    Tokens record their owner inside inverse_args (every inverse command
    takes user_id), so ownership is enforced at dispatch time too."""
    out: list[UndoToken] = []
    for tid in reversed(_ORDER):
        tok = get_token(tid)
        if tok is None:
            continue
        if int((tok.inverse_args or {}).get("user_id", -1)) == int(user_id):
            out.append(tok)
            if len(out) >= max(1, int(limit)):
                break
    return out


def execute_undo(token_id: str) -> "UndoOutcome":
    """Dispatch a stored token to its inverse command (idempotent)."""
    from services.commands import UNDO_COMMANDS, CommandError

    tok = get_token(token_id)
    if tok is None:
        return UndoOutcome(ok=False, changed=False,
                           message="This undo offer expired or was already used.")
    fn = UNDO_COMMANDS.get(tok.inverse_command)
    if fn is None:
        log.warning("undo: unknown inverse command %s", tok.inverse_command)
        return UndoOutcome(ok=False, changed=False,
                           message="Undo is unavailable for this action.")
    try:
        res = fn(**dict(tok.inverse_args))
    except CommandError as e:
        return UndoOutcome(ok=False, changed=False, message=str(e))
    except Exception as e:                       # pragma: no cover - defensive
        log.warning("undo %s failed: %s", tok.inverse_command, e)
        return UndoOutcome(ok=False, changed=False,
                           message="The undo could not be applied.")
    msg = ("Undone." if res.changed else "Nothing to change — already undone.")
    return UndoOutcome(ok=True, changed=bool(res.changed),
                       revision=res.revision, message=msg)


@dataclass(frozen=True)
class UndoOutcome:
    ok: bool
    changed: bool
    revision: int | None = None
    message: str = ""
