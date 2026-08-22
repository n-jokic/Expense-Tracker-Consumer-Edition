"""
ai/safety.py — advisor safety boundary (Phase 3 A7) + outbound privacy
sanitizer (AI-01).

READ-ONLY initially. No direct SQL. No direct service mutation.
Proposed mutations must be confirmed via UI confirmation button that calls
the command service directly — the model never executes them.

AI-01 privacy boundary — this module is THE single sanitizer boundary for
everything that leaves the machine toward an external provider:

  • ``sanitize_tool_result(obj, external=...)`` — structured pass over tool
    results before they are serialized into a prompt. Credential-bearing and
    sensitive-keyed fields are redacted in BOTH modes; identifiers, account
    metadata, local paths, and emails are removed only in external mode.
  • ``sanitize_outbound_text(text)`` — string-level last line of defense,
    applied unconditionally at the one place a request leaves the device
    (``llm._api_chat``). Redacts credentials, home/workspace paths, emails.
  • ``strip_credentials(text)`` — credential-only pass used on the LOCAL
    provider path (``llm._local_chat``): local models keep the user's own
    context (paths/emails/financial rows stay on device), but embedded
    credentials are still stripped.

Privacy behavior, documented accurately:
  - External provider ("api", OpenAI-compatible endpoint): prompts contain
    only sanitized aggregates/fields needed for the answer; row IDs and
    account metadata are dropped, descriptions are capped, and credentials /
    absolute user paths / emails are replaced by fixed markers. Redaction is
    logged at DEBUG level as counts only — never values.
  - Local provider (llama.cpp GGUF on device): no data leaves the machine,
    so local context (paths, emails, raw rows) is preserved verbatim; only
    credential-shaped strings are stripped. The sanitizer never logs secret
    values in either mode.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("ai.safety")

# Tools are read-only; this set is intentionally empty until explicit
# user-confirmed mutations are introduced.
ALLOWED_MUTATIONS: set[str] = set()

# Patterns that must never appear in model output that claims to be a tool call
# (prevents prompt-injection trying to exfiltrate or mutate).
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"DROP\s+TABLE", re.I),
    re.compile(r"DELETE\s+FROM", re.I),
    re.compile(r"UPDATE\s+\w+\s+SET", re.I),
    re.compile(r"INSERT\s+INTO", re.I),
    re.compile(r";\s*--"),
]

# Maximum argument string length to prevent prompt injection via huge payloads
MAX_ARGUMENT_VALUE_LEN = 500


def is_read_only_tool(tool: str) -> bool:
    """True if tool is in read-only allowlist."""
    try:
        from ai.tool_registry import TOOLS
        return tool in TOOLS
    except Exception:
        return False


def sanitize_question(question: str) -> str:
    """Strip newlines, cap length, neutralize instruction-injection patterns."""
    if not question:
        return ""
    # Collapse newlines so stored/hostile strings cannot inject instructions
    s = str(question).replace("\r", " ").replace("\n", " ")
    # Cap to 500 chars — longer questions are truncated, not rejected
    if len(s) > MAX_ARGUMENT_VALUE_LEN:
        s = s[:MAX_ARGUMENT_VALUE_LEN]
    return s.strip()


def validate_no_sql(text: str) -> tuple[bool, str | None]:
    """Check tool output for SQL injection patterns."""
    if not text:
        return True, None
    for pat in _BLOCKED_PATTERNS:
        if pat.search(text):
            return False, f"blocked pattern: {pat.pattern}"
    return True, None


def check_mutation_proposal(question: str) -> dict | None:
    """Detect if user is asking for a mutation (e.g. 'Set my Dining budget to €350').

    Returns a proposal dict with type/args for the UI confirm button, or None.
    The model never executes it — only the confirmation button calls
    budget_commands.set_budget etc. (A7 safety).
    """
    q = question.lower()
    # Budget change intent
    m = re.search(r"set.*budget.*?(\d+(?:\.\d+)?)", q)
    if m:
        amount = m.group(1)
        # Try to extract category
        cats = [
            "housing & utilities", "groceries", "dining out", "transport", "travel",
            "entertainment", "shopping", "subscriptions & software", "fees & taxes",
            "loans & debt", "health", "other",
        ]
        cat = None
        for c in cats:
            if c in q:
                cat = c.title() if c != "housing & utilities" else "Housing & Utilities"
                # Fix title casing for special categories
                if c == "subscriptions & software":
                    cat = "Subscriptions & Software"
                elif c == "fees & taxes":
                    cat = "Fees & Taxes"
                elif c == "loans & debt":
                    cat = "Loans & Debt"
                break
        return {
            "type": "budget_change",
            "category": cat,
            "amount_eur": float(amount),
            # Pin the period NOW so confirmation applies what was proposed,
            # not whatever month happens to be current when clicked (L3).
            "year": __import__("datetime").date.today().year,
            "month": __import__("datetime").date.today().month,
            "proposed": True,
            "requires_confirmation": True,
            "message": f"Proposed change: set {cat or 'category'} budget to €{amount} — confirm to apply.",
        }
    return None


def tool_result_with_provenance_check(result: dict) -> bool:
    """Verify tool result carries _provenance (A3 gate)."""
    return isinstance(result, dict) and "_provenance" in result


_WHITESPACE_COLLAPSE = re.compile(r"\s+")

# Cap for individual sanitised string leaves in tool results.
MAX_UNSANITIZED_STR_LEN = 200
_MAX_SANITIZE_DEPTH = 6


def sanitize_untrusted_text(value: Any, max_len: int = MAX_UNSANITIZED_STR_LEN) -> str:
    """Make a tool-result string leaf safe for prompt embedding.

    Mirrors llm._sanitize_stat semantics exactly: newlines become spaces,
    internal whitespace runs are collapsed, and the result is hard-capped
    to max_len characters so stored data cannot inject instructions into
    the prompt."""
    if not isinstance(value, str):
        value = str(value)
    s = value.replace("\r", " ").replace("\n", " ")
    s = _WHITESPACE_COLLAPSE.sub(" ", s)
    if len(s) > max_len:
        s = s[:max_len]
    return s.strip()


def sanitize_tool_result(obj: Any, external: bool = True, _depth: int = 0) -> Any:
    """Recursively sanitize untrusted tool results before prompt embedding.

    dict/list/tuple containers are preserved; every str leaf is sanitized via
    sanitize_untrusted_text; non-string leaves are returned untouched.
    Recursion is depth-capped to defend against pathological nesting.

    AI-01 modes (fail closed — the default is ``external=True``):
      - ``external=True`` (payload may reach a cloud provider): string leaves
        additionally have credentials, absolute user paths, and emails
        redacted; id-like keys (``id``, ``user_id``, ``*_id``) are dropped and
        sensitive-keyed values replaced by ``[REDACTED]``.
      - ``external=False`` (local on-device model): local context is kept
        verbatim, but credential-shaped strings and sensitive-keyed values
        are still redacted.

    Redaction counts are logged at DEBUG level — never the redacted values.
    """
    stats = _RedactionStats()
    out = _sanitize_walk(obj, external, _depth, stats)
    stats.log_debug("sanitize_tool_result")
    return out


# ── AI-01: outbound privacy sanitizer ────────────────────────────────────────

REDACTED_CREDENTIAL = "[CREDENTIAL_REDACTED]"
REDACTED_PATH = "[LOCAL_PATH]"
REDACTED_EMAIL = "[EMAIL_REDACTED]"
REDACTED_VALUE = "[REDACTED]"

# Fixed replacement markers never re-match any pattern below, which makes the
# whole pipeline deterministic and idempotent.

# Credential-shaped strings. Order matters only for count labels; every
# pattern uses a replacement callback so surrounding text is preserved.
_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    # OpenAI-style keys (also OpenRouter sk-or-v1-...)
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    # GitHub / Slack / AWS access tokens
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # JWT (three base64url segments)
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # Long hex secrets / API digests (unbounded so partial runs never leak)
    ("hex_secret", re.compile(r"\b[a-fA-F0-9]{32,}\b")),
    # "Bearer <token>" authorization headers
    ("bearer_token",
     re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{10,}\b")),
    # keyword=value / "keyword": "value" assignments carrying secrets.
    # The lookbehind (not \b) also matches compound keys like "api_token";
    # an optional quote between keyword and separator covers the JSON form
    # '"token": "..."'; the value class excludes brackets/quotes so the fixed
    # markers written here are never re-matched (idempotency).
    ("keyword_assignment",
     re.compile(r"(?i)(?<![A-Za-z0-9])(api[_-]?key|apikey|secret|token|"
                r"password|passwd|authorization|credential[s]?)\b\s*([\"']?)"
                r"\s*([:=])\s*([\"']?)([^\s\"',;}\[\]]{4,})")),
]

# Absolute user paths: Windows home dirs (both slash styles), macOS/Linux
# home dirs, and /root. Workspace/project paths live under the user's home
# directory and are therefore covered by the same patterns.
_PATH_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("windows_home_path",
     re.compile(r"[A-Za-z]:[/\\]+(?:Users|Documents and Settings)[/\\]+"
                r"[^\s\"'<>|]+")),
    ("posix_home_path",
     re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+(?:/[^\s\"'<>|]*)?")),
    ("root_home_path", re.compile(r"/root/(?:[^\s\"'<>|]*)?")),
]

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Trailing punctuation that belongs to the sentence, not to a matched path.
_PATH_TRAILING_PUNCT = ".,;:!?)»…"


def _replace_marker(marker: str):
    def _sub(match: re.Match) -> str:
        return marker
    return _sub


def _sub_path(match: re.Match) -> str:
    """Replace a path with [LOCAL_PATH], keeping sentence punctuation that
    the greedy character class swallowed."""
    text = match.group(0)
    trimmed = text.rstrip(_PATH_TRAILING_PUNCT)
    return REDACTED_PATH + text[len(trimmed):]


def _sub_keyword_assignment(match: re.Match) -> str:
    """Keep the keyword ('api_key='), redact only its value; preserve any
    surrounding quotes so JSON-ish structure stays intact."""
    keyword, key_close_quote, sep, value_quote, _value = match.groups()
    close = value_quote if value_quote in "\"'" else ""
    return f"{keyword}{key_close_quote}{sep}{value_quote}{REDACTED_CREDENTIAL}{close}"


class _RedactionStats:
    """Per-call counters; logged at DEBUG as counts, never values."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def add(self, name: str, n: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + n

    def log_debug(self, where: str) -> None:
        if self.counts:
            log.debug("%s redactions (%s): %s", where,
                      sum(self.counts.values()), dict(sorted(self.counts.items())))


def redact_credentials(text: str, stats: _RedactionStats | None = None) -> str:
    """Strip credential-shaped substrings from one string leaf."""
    for name, pat in _CREDENTIAL_PATTERNS:
        before = text
        text = (pat.sub(_sub_keyword_assignment, text)
                if name == "keyword_assignment"
                else pat.sub(_replace_marker(REDACTED_CREDENTIAL), text))
        if stats is not None and text != before:
            stats.add(name)
    return text


def redact_outbound(text: str, stats: _RedactionStats | None = None) -> str:
    """Full external-mode pass over one string: credentials + paths + emails."""
    own = stats is None
    st = stats if stats is not None else _RedactionStats()
    text = redact_credentials(text, st)
    for name, pat in _PATH_PATTERNS:
        if pat.search(text):
            text = pat.sub(_sub_path, text)
            st.add(name)
    if _EMAIL_PATTERN.search(text):
        text = _EMAIL_PATTERN.sub(_replace_marker(REDACTED_EMAIL), text)
        st.add("email")
    if own:
        st.log_debug("redact_outbound")
    return text


def strip_credentials(text: str) -> str:
    """Credential-only pass for the LOCAL provider path: local models keep
    the user's context, but embedded API keys/tokens are still stripped."""
    st = _RedactionStats()
    out = redact_credentials(str(text or ""), st)
    st.log_debug("strip_credentials")
    return out


def sanitize_outbound_text(text: str) -> str:
    """THE string-level boundary for payloads leaving the machine.

    Applied unconditionally at the single egress point (llm._api_chat) as the
    last line of defense behind the structured sanitize_tool_result pass.
    Deterministic and idempotent; logs redaction COUNTS at debug level, never
    the redacted values."""
    st = _RedactionStats()
    out = redact_outbound(str(text or ""), st)
    st.log_debug("sanitize_outbound_text")
    return out


def redact_with_counts(text: str) -> tuple[str, dict[str, int]]:
    """Full external-mode pass that also returns the per-category redaction
    counts (for tests and diagnostics). Same transformation as
    sanitize_outbound_text."""
    st = _RedactionStats()
    out = redact_outbound(str(text or ""), st)
    return out, dict(sorted(st.counts.items()))


# ── Structured field policy ──────────────────────────────────────────────────

# Keys whose value must never be serialized anywhere, in any mode.
_SENSITIVE_KEY_EXACT = {
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "api_key", "apikey", "authorization", "credentials", "credential", "auth",
}
_SENSITIVE_KEY_SUFFIXES = (
    "_key", "_token", "_secret", "_password", "_passwd", "_credentials",
)

# Identifier/account-metadata keys dropped from EXTERNAL prompts only — the
# model does not need them to answer, and they must not leave the machine.
_ID_KEY_EXACT = {"id", "uid", "uuid", "guid", "user_id", "userid"}
_ID_KEY_SUFFIXES = ("_id", "_uuid", "_guid")


def _is_sensitive_key(key: str) -> bool:
    k = str(key).lower()
    return k in _SENSITIVE_KEY_EXACT or k.endswith(_SENSITIVE_KEY_SUFFIXES)


def _is_id_key(key: str) -> bool:
    k = str(key).lower()
    return k in _ID_KEY_EXACT or k.endswith(_ID_KEY_SUFFIXES)


def _sanitize_leaf_str(value: str, external: bool, stats: _RedactionStats) -> str:
    """One string leaf: redact (per mode), then collapse whitespace/cap."""
    s = redact_credentials(value, stats)
    if external:
        s = redact_outbound(s, stats)
    return sanitize_untrusted_text(s)


def _sanitize_walk(obj: Any, external: bool, depth: int,
                   stats: _RedactionStats) -> Any:
    if depth > _MAX_SANITIZE_DEPTH:
        return obj
    if isinstance(obj, str):
        return _sanitize_leaf_str(obj, external, stats)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_sensitive_key(k):
                # Keep the shape, destroy the value — visible, not silent.
                out[k] = REDACTED_VALUE
                stats.add("sensitive_field")
                continue
            if external and isinstance(k, str) and _is_id_key(k):
                out[k] = REDACTED_VALUE
                stats.add("identifier_field")
                continue
            out[k] = _sanitize_walk(v, external, depth + 1, stats)
        return out
    if isinstance(obj, list):
        return [_sanitize_walk(v, external, depth + 1, stats) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_walk(v, external, depth + 1, stats) for v in obj)
    return obj
