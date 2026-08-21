"""
AI-02 regression tests — deterministic planner-argument repair.

Missing year/month fill from the original question/current date BEFORE any
model repair; explicit and "last month" dates stay correct; numeric-string
arguments coerce; ambiguous periods become clarifications; provider
capabilities no longer claim unimplemented native tools/schema.
"""

from datetime import date

import pytest

from ai.router import repair_missing_dates


# ── Missing period filled deterministically ──────────────────────────────────

def test_missing_year_month_filled_from_current_date():
    today = date(2026, 2, 15)
    args, amb = repair_missing_dates("aggregate_spending", {}, "How much did I spend?", today)
    assert (args["year"], args["month"]) == (2026, 2)
    assert amb is False


def test_explicit_month_respected():
    today = date(2026, 2, 15)
    args, _ = repair_missing_dates(
        "aggregate_spending", {}, "How much did I spend in March 2025?", today)
    assert (args["year"], args["month"]) == (2025, 3)


def test_last_month_respected_including_january_edge():
    args, _ = repair_missing_dates(
        "aggregate_spending", {}, "spending last month", date(2026, 1, 10))
    assert (args["year"], args["month"]) == (2025, 12)
    args, _ = repair_missing_dates(
        "aggregate_spending", {}, "spending last month", date(2026, 3, 10))
    assert (args["year"], args["month"]) == (2026, 2)


def test_existing_arguments_never_overwritten():
    today = date(2026, 2, 15)
    args, _ = repair_missing_dates(
        "aggregate_spending", {"year": 2024}, "in March 2025", today)
    assert args["year"] == 2024          # kept
    assert args["month"] == 3            # only the missing half filled


def test_tools_without_period_args_untouched():
    args, _ = repair_missing_dates("savings_status", {}, "in March 2025", None)
    assert "year" not in args and "month" not in args


# ── Numeric-string coercion ──────────────────────────────────────────────────

def test_numeric_strings_coerced():
    args, _ = repair_missing_dates(
        "merchant_breakdown",
        {"year": "2025", "month": "3", "n": "10"},
        "top merchants March 2025", None)
    assert args["year"] == 2025 and args["month"] == 3 and args["n"] == 10


def test_currency_string_coerced_to_float():
    args, _ = repair_missing_dates(
        "purchase_scenario", {"purchase_eur": "1,234.56"}, "afford 1,234.56 EUR", None)
    assert args["purchase_eur"] == 1234.56


def test_uncoercible_string_left_for_validation():
    args, _ = repair_missing_dates(
        "aggregate_spending", {"year": "not-a-year"}, "spending", None)
    assert args["year"] == "not-a-year"   # validator must reject it later


# ── Ambiguity detection ──────────────────────────────────────────────────────

def test_two_different_months_named_is_ambiguous():
    args, amb = repair_missing_dates(
        "aggregate_spending", {}, "show my spending in March and June", None)
    assert amb is True


def test_one_month_or_none_is_not_ambiguous():
    _, amb1 = repair_missing_dates("aggregate_spending", {}, "spending in March", None)
    _, amb2 = repair_missing_dates("aggregate_spending", {}, "spending this month", None)
    assert amb1 is False and amb2 is False


# ── Repair prompt carries full context ───────────────────────────────────────

def test_repair_prompt_includes_question_schema_and_error():
    from ai.prompts import repair_prompt
    p = repair_prompt("spending in March?", '{"tool": "aggregate_spending"}',
                      error="argument year must be int",
                      schema_text='{"required": ["year", "month"]}')
    assert "spending in March?" in p
    assert '"required"' in p
    assert "argument year must be int" in p
    assert '{"tool": "aggregate_spending"}' in p


# ── Provider capabilities tell the truth (AI-02 criterion 3) ────────────────

def test_openai_compatible_capabilities_do_not_claim_native_tools():
    from ai.providers.openai_compatible import OpenAICompatibleProvider
    caps = OpenAICompatibleProvider.capabilities
    assert caps.native_tool_calls is False
    assert caps.json_schema is False


# ── Orchestrator-level behavior with a scripted provider ────────────────────

class _ScriptedProvider:
    """Returns queued texts regardless of request."""
    def __init__(self, texts):
        self._texts = list(texts)

    def generate(self, request):
        from ai.providers.base import GenerationResult
        text = self._texts.pop(0) if self._texts else ""
        return GenerationResult(text=text)


def _patch_provider(monkeypatch, texts):
    import ai.orchestrator as orch
    monkeypatch.setattr(orch, "_get_provider", lambda settings: _ScriptedProvider(texts))
    # Keep the sanitizer mode deterministic (local = non-external).
    monkeypatch.setattr(orch, "_external_provider", lambda settings: False)


def test_ambiguous_period_becomes_clarification(monkeypatch):
    _patch_provider(monkeypatch, [
        '{"tool": "aggregate_spending", "arguments": {}}',
    ])
    from ai.orchestrator import orchestrate
    res = orchestrate(1, "show my spending in March and June", {})
    assert res["error"] is None
    assert res["diagnostic"].startswith("planner clarification")
    assert "Which month do you mean" in res["answer"]
    assert res["tool_calls"] == []


def test_unresolved_argument_error_becomes_clarification(monkeypatch):
    bad = '{"tool": "aggregate_spending", "arguments": {"year": "not-a-year"}}'
    _patch_provider(monkeypatch, [bad, bad])   # initial + repair round
    from ai.orchestrator import orchestrate
    # NB: a question that does NOT match any deterministic fast-route
    # pattern, so the scripted planner is actually consulted.
    res = orchestrate(1, "detail my outgoings please", {})
    assert res["error"] is None
    assert res["diagnostic"] == "planner clarification: unresolved arguments"
    assert "couldn't work out the details" in res["answer"]


def test_fast_route_still_wins_over_planner_for_known_questions():
    """Guard: common questions keep their zero-model deterministic path."""
    from ai.router import fast_route
    assert fast_route("how much did I spend this month?") == "aggregate_spending"
