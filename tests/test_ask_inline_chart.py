"""
tests/test_ask_inline_chart.py — AI-04 #19 regression tests.

Charts must render INLINE in Ask-your-data chat, and the system prompt must
stop models claiming they cannot plot.
"""

import pytest

from ai.charts import validate_chart_spec
from ai.prompts import ADVISOR_SYSTEM


# ── System prompt: must mention chart rendering, never self-deny ──────────────

def test_advisor_system_mentions_chart_rendering():
    # #19-1: the system prompt must tell models the chart is rendered for them
    assert "chart" in ADVISOR_SYSTEM.lower() or "plot" in ADVISOR_SYSTEM.lower()
    assert "rendered below" in ADVISOR_SYSTEM.lower()


@pytest.mark.parametrize("denial", [
    "i cannot create", "i cannot show", "i am unable to plot",
    "unable to display", "can't plot",
])
def test_advisor_system_has_no_self_denying_plot_wording(denial):
    low = ADVISOR_SYSTEM.lower()
    assert denial not in low, (
        f"ADVISOR_SYSTEM must not contain self-denying phrase: {denial!r}")


def test_advisor_system_forbids_claiming_inability():
    # Explicit guard: the prompt must forbid the model from claiming inability
    assert "never claim" in ADVISOR_SYSTEM.lower()
    assert "cannot create or show plots" in ADVISOR_SYSTEM.lower()


# ── Orchestrator: "make me a plot of my spendings" yields a _chart spec ────────

def test_orchestrator_plot_question_attaches_validated_chart(monkeypatch):
    """#19-2: "make me a plot of my spendings" fast-routes to __series__,
    which attaches a validated _chart dict to the tool result."""
    import ai.tool_registry as tr
    import ai.orchestrator as orch

    rows = [
        {"month": "2025-01", "amount_eur": 100.0},
        {"month": "2025-02", "amount_eur": 250.5},
        {"month": "2025-03", "amount_eur": 75.25},
    ]

    def fake_series(user_id, months=12):
        return {
            "series": rows,
            "_provenance": {
                "calculation": "spending_series", "row_count": 3,
                "currency_basis": "EUR",
            },
        }

    monkeypatch.setitem(tr.TOOLS, "spending_series", fake_series)

    res = orch.orchestrate(1, "make me a plot of my spendings", {}, history=None)

    tool_calls = res.get("tool_calls") or []
    assert tool_calls, "expected a tool call to be produced"
    tc = tool_calls[0]
    assert tc["tool"] == "spending_series"

    result = tc["result"]
    assert isinstance(result.get("_chart"), dict), (
        "tool result must carry a validated _chart dict")

    # The chart spec must be the canonical validated form, not raw model output.
    spec = validate_chart_spec(
        result["_chart"], result.get("series") or [])
    assert spec is not None
    assert spec["type"] == "line"
    assert spec["x"] == "month"
    assert spec["y"] == "amount_eur"
    # Data equals the canonical series rows — nothing invented.
    assert spec["data"] == rows


def test_orchestrator_plot_question_no_provider_still_attaches_chart(monkeypatch):
    """#19-2b: fast-route attaches _chart even without an LLM provider."""
    import ai.tool_registry as tr
    import ai.orchestrator as orch

    rows = [{"month": "2025-01", "amount_eur": 42.0}]

    def fake_series(user_id, months=12):
        return {
            "series": rows,
            "_provenance": {
                "calculation": "spending_series", "row_count": 1,
                "currency_basis": "EUR",
            },
        }

    monkeypatch.setitem(tr.TOOLS, "spending_series", fake_series)

    res = orch.orchestrate(1, "make me a plot of my spendings", {}, history=None)
    tc = res["tool_calls"][0]
    assert isinstance(tc["result"].get("_chart"), dict)


# ── Helper: _render_chart_from_result degrades gracefully ─────────────────────
# app_pages/ask.py accesses st.session_state at module level, so we mock it
# with a dict-like shim (mirrors test_cache_revision.py _SessionState pattern)
# before importing the module.

class _SessionState(dict):
    """dict with attribute access — mimics st.session_state's interface."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value


@pytest.fixture()
def ask_module(monkeypatch):
    """Import app_pages/ask.py with a mocked st.session_state so the
    module-level session_state access does not raise."""
    import streamlit as st
    import sys

    ss = _SessionState({
        "user_id": 1,
        "settings": {"ai_provider": "none"},
        "ask_history": [],
        "_last_tool_calls": [],
    })
    monkeypatch.setattr(st, "session_state", ss)

    # Ensure fresh import (may have been imported by another test)
    sys.modules.pop("app_pages.ask", None)
    import app_pages.ask
    return app_pages.ask


def test_render_chart_from_result_handles_invalid_spec(ask_module, monkeypatch):
    """#19-3: invalid/missing spec renders nothing extra (no exception)."""
    rendered = []
    monkeypatch.setattr(ask_module.st, "plotly_chart",
                        lambda fig, *a, **k: rendered.append(fig))

    # Missing _chart → no call
    ask_module._render_chart_from_result({"total_eur": 100.0})
    assert rendered == []

    # Invalid _chart type → no call, no exception
    ask_module._render_chart_from_result({"_chart": "not-a-dict", "series": []})
    assert rendered == []

    # Invalid spec (bad type) with real series → no call
    ask_module._render_chart_from_result({
        "_chart": {"type": "scatter3d", "x": "month", "y": "amount_eur"},
        "series": [{"month": "2025-01", "amount_eur": 1.0}],
    })
    assert rendered == []


def test_render_chart_from_result_renders_valid_spec(ask_module, monkeypatch):
    """#19-3b: a valid _chart dict with matching series renders exactly once."""
    rendered = []
    monkeypatch.setattr(ask_module.st, "plotly_chart",
                        lambda fig, *a, **k: rendered.append(fig))

    ask_module._render_chart_from_result({
        "_chart": {"type": "line", "title": "Spending by month",
                   "x": "month", "y": "amount_eur"},
        "series": [
            {"month": "2025-01", "amount_eur": 100.0},
            {"month": "2025-02", "amount_eur": 250.5},
        ],
    })
    assert len(rendered) == 1
