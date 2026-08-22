"""
#18 — tests for the collapsible Insights-page rendering helpers.

Verifies `$summary_for` (the pure label-extraction helper) and confirms the
severity→icon mapping. These are pure-function unit tests that mirror the style
of tests/test_insights.py; they do not require a Streamlit runtime.
"""

from insights import _summary_for, _SEVERITY_ICON


# ── _summary_for: em-dash split ──────────────────────────────────────────────

def test_summary_emdash_picks_head_before_emdash():
    """A short prefix before the em-dash becomes the summary label."""
    text = "Travel budget: **€120** of **€500** this year — 24% used, 50% gone."
    assert _summary_for("info", text) == "Travel budget: **€120** of **€500** this year"


def test_summary_emdash_long_head_falls_back_to_truncation():
    """#18 — when the head before the em-dash exceeds 80 chars, fall back to 70+ellipsis."""
    long_head = "x" * 90
    text = long_head + " — trailing detail"
    result = _summary_for("warning", text)
    assert result == "x" * 70 + "…"


def test_summary_emdash_exactly_80_chars_kept():
    """Boundary: exactly 80 chars before the em-dash is kept unchanged."""
    head = "y" * 80
    text = head + " — detail"
    assert _summary_for("success", text) == head


def test_summary_emdash_81_chars_falls_back():
    """Boundary: 81 chars before the em-dash forces truncation fallback."""
    head = "z" * 81
    text = head + " — detail"
    result = _summary_for("error", text)
    assert result == "z" * 70 + "…"


# ── _summary_for: long-text fallback ─────────────────────────────────────────

def test_summary_long_text_without_emdash_truncated():
    """#18 — text with no em-dash and >70 chars is truncated to 70 + ellipsis."""
    text = "This is a really long insight message that definitely exceeds seventy characters "
    result = _summary_for("info", text)
    assert result.endswith("…")
    assert len(result) == 71  # 70 chars + 1 ellipsis


def test_summary_short_text_passthrough():
    """#18 — short text (<=70 chars) with no em-dash passes through unchanged."""
    text = "Fun money on track: **€40** left this month."
    assert _summary_for("success", text) == text


def test_summary_empty_text_returns_empty():
    """#18 — empty text yields an empty summary (no crash)."""
    assert _summary_for("info", "") == ""


def test_summary_emdash_only_at_start():
    """#18 — an em-dash at the very start yields an empty head (still <=80 chars)."""
    text = "— leading detail with no real summary prefix that is very long indeed"
    result = _summary_for("error", text)
    # head before the em-dash is "" (empty) and "" <= 80 chars, so it is returned as-is
    assert result == ""


# ── severity icon mapping ────────────────────────────────────────────────────

def test_severity_icons_cover_all_card_types():
    """Every card type produced by render_insights() has a mapped material icon."""
    # Card types used throughout render_insights(): success, warning, error, info.
    for card_type in ("success", "warning", "error", "info"):
        assert card_type in _SEVERITY_ICON, f"missing icon for {card_type}"


def test_severity_icons_use_material_namespace():
    """All mapped icons use the :material/ namespace consistent with the rest of the file."""
    for name, icon in _SEVERITY_ICON.items():
        assert icon.startswith(":material/"), f"{name} icon lacks :material/ prefix"


def test_summary_is_pure_function():
    """#18 — _summary_for returns a str for any input and has no Streamlit side-effects."""
    result = _summary_for("warning", "Spent more — €200")
    assert isinstance(result, str)
