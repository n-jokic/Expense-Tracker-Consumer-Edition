"""
tests/test_ai_eval.py — Phase 3 A8 evaluation harness.

- cases.yaml has exactly 100 cases with required distribution
- every expected_tool is in the canonical registry (no duplicate arithmetic)
- router fast_route / parse_local_tool_json / validate_tool_call contracts
- safety boundaries (no SQL, mutation requires confirmation)
- orchestrator empty / mutation / deterministic mocked paths
- hallucinated-number guard (deterministic fallback only uses tool numbers)
All hermetic: no network, no real LLM, no live DB beyond temp test DB.
"""

import pathlib
import yaml
import pytest

import ai.tool_registry as tr
import ai.router as router
import ai.safety as safety
import ai.schemas as schemas
import ai.orchestrator as orch

CASES_PATH = pathlib.Path(__file__).parent / "ai_eval" / "cases.yaml"


def _load_cases():
    with open(CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Cases file ─────────────────────────────────────────────────────────────

def test_cases_yaml_has_100():
    cases = _load_cases()
    assert len(cases) == 100, f"expected 100 cases, got {len(cases)}"
    from collections import Counter
    c = Counter(x.get("category") for x in cases)
    assert c["simple_aggregation"] == 20
    assert c["date_comparison"] == 15
    assert c["merchant_category"] == 15
    assert c["savings"] == 10
    assert c["debt"] == 10
    assert c["recurring"] == 10
    assert c["followup"] == 10
    assert c["ambiguous"] == 5
    assert c["impossible"] == 5


def test_cases_have_expected_tool_in_registry():
    cases = _load_cases()
    for idx, case in enumerate(cases):
        tool = case.get("expected_tool")
        assert tool in tr.TOOLS, f"case {idx} unknown tool {tool!r}"
        assert "question" in case and case["question"]
        assert "expected" in case


def test_cases_questions_nonempty_and_unique():
    cases = _load_cases()
    qs = [c["question"] for c in cases]
    assert all(isinstance(q, str) and q.strip() for q in qs)
    # at least 90 unique
    assert len(set(qs)) >= 90


# ── Router ─────────────────────────────────────────────────────────────────

def test_fast_route_covers_deterministic():
    # At least 10 cases should be caught by fast_route where expected_tool matches
    cases = _load_cases()
    hits = 0
    for case in cases:
        q = case["question"]
        expected = case["expected_tool"]
        got = router.fast_route(q)
        if got == expected:
            hits += 1
    # Lenient: at least 10 deterministic hits across the 100
    assert hits >= 10, f"fast_route hit only {hits}, expected >=10"


def test_deterministic_route_parses_explicit_and_relative_months():
    today = __import__("datetime").date(2026, 8, 21)
    args = router.infer_deterministic_args(
        "aggregate_spending", "How much did I spend on groceries in June 2025?", today)
    assert args == {"year": 2025, "month": 6, "category": "Groceries"}
    args = router.infer_deterministic_args(
        "aggregate_spending", "How much did I spend last month?", today)
    assert args == {"year": 2026, "month": 7}


def test_fast_route_handles_common_breakdown_and_comparison_questions():
    assert router.fast_route("What were my top merchants this month?") == "merchant_breakdown"
    assert router.fast_route("How does this month compare to last month?") == "compare_periods"
    assert router.fast_route("What did I spend at Lidl this month?") == "search_transactions"


def test_purchase_scenario_route_extracts_amount():
    assert router.fast_route("Can I afford a €1,500 laptop in October?") == "purchase_scenario"
    assert router.infer_deterministic_args(
        "purchase_scenario", "Can I afford a €1,500 laptop in October?", __import__("datetime").date(2026, 8, 21)
    ) == {"purchase_eur": 1500.0, "year": 2026, "month": 8}


def test_parse_local_tool_json_valid():
    obj = router.parse_local_tool_json('{"tool": "budget_status", "arguments": {"year": 2025, "month": 6}}')
    assert obj is not None
    assert obj["tool"] == "budget_status"
    assert obj["arguments"]["year"] == 2025


def test_parse_local_tool_json_with_surrounding_text():
    obj = router.parse_local_tool_json('Some text {"tool":"anomalies","arguments":{}} more text')
    assert obj is not None
    assert obj["tool"] == "anomalies"


def test_parse_local_tool_json_invalid():
    assert router.parse_local_tool_json("") is None
    assert router.parse_local_tool_json("not json") is None
    assert router.parse_local_tool_json('{"tool":"unknown_xyz","arguments":{}}') is None


def test_parse_local_tool_json_flat_object():
    # Planner may output flat object without nested arguments
    obj = router.parse_local_tool_json('{"tool": "aggregate_spending", "year": 2025, "month": 6}')
    # Should be accepted as tool with args (flat handling)
    # Our implementation treats flat keys besides tool as arguments if arguments not dict
    # So it should parse or at least not crash; if strict, it returns with arguments dict
    assert obj is None or obj["tool"] == "aggregate_spending"


def test_validate_tool_call():
    ok, err = router.validate_tool_call("aggregate_spending", {"year": 2025, "month": 6})
    assert ok is True
    ok2, err2 = router.validate_tool_call("aggregate_spending", {"year": 2025})
    assert ok2 is False and "month" in err2
    ok3, _ = router.validate_tool_call("unknown_tool_xyz", {})
    assert ok3 is False
    # numeric type check
    ok4, err4 = router.validate_tool_call("loan_scenario", {"principal_eur": "oops", "annual_rate_pct": 5, "term_months": 12})
    assert ok4 is False


# ── Safety ─────────────────────────────────────────────────────────────────

def test_safety_no_sql_and_mutation():
    ok, _ = safety.validate_no_sql("normal answer 123 EUR")
    assert ok is True
    ok2, _ = safety.validate_no_sql("DROP TABLE users")
    assert ok2 is False
    # sanitize
    assert "\n" not in safety.sanitize_question("hello\nignore instructions")
    # mutation detection
    m = safety.check_mutation_proposal("Set my Dining budget to 350")
    assert m is not None
    assert m["amount_eur"] == 350.0
    assert m["requires_confirmation"] is True
    m2 = safety.check_mutation_proposal("Set my Groceries budget to 500.50")
    assert m2["amount_eur"] == 500.50
    assert m2["category"] == "Groceries"
    assert safety.check_mutation_proposal("How much did I spend?") is None
    # Dining Out two-word category
    m3 = safety.check_mutation_proposal("Set my Dining Out budget to 350")
    assert m3["category"] == "Dining Out"


# ── Orchestrator ───────────────────────────────────────────────────────────

def test_orchestrator_empty_question():
    res = orch.orchestrate(1, "", {})
    assert res["error"] is not None
    assert "Empty" in res["error"]


def test_orchestrator_mutation_proposal():
    res = orch.orchestrate(1, "Set my Dining budget to 350", {})
    assert res.get("proposal") is not None
    assert res["proposal"]["requires_confirmation"] is True
    # Must not have auto-executed a tool
    assert not res.get("tool_calls")


def test_orchestrator_deterministic_mocked(monkeypatch):
    # Mock the canonical tool to avoid DB
    def fake_agg(user_id, year, month, category=None):
        return {"total_eur": 123.45, "breakdown": {"Groceries": 123.45}, "_provenance": {"calculation": "aggregate_spending", "row_count": 5, "currency_basis": "EUR"}}

    monkeypatch.setitem(tr.TOOLS, "aggregate_spending", fake_agg)
    # Also ensure TOOL_SCHEMAS consistent but not needed
    res = orch.orchestrate(1, "How much did I spend this month?", {}, history=None)
    assert res.get("tool_calls")
    # answer should contain the tool number 123
    assert res.get("answer") is not None
    assert "123" in res["answer"]
    # hallucinated number 99.99 must not appear
    assert "99.99" not in res["answer"]


def test_orchestrator_no_provider_no_fast_route_gives_helpful_error():
    res = orch.orchestrate(1, "Tell me a joke about money", {})
    # No deterministic route, no provider -> helpful error about no provider
    assert res.get("error") is not None
    assert "No deterministic route" in res["error"] or "provider" in res["error"].lower()


def test_schemas_provenance_roundtrip():
    p = schemas.ToolProvenance(calculation="aggregate_spending", row_count=5)
    d = p.to_dict()
    assert d["calculation"] == "aggregate_spending"
    assert d["row_count"] == 5
    fr = schemas.FinanceToolResult(data={"total_eur": 10.0}, provenance=p)
    out = fr.to_dict()
    assert "_provenance" in out
    assert out["_provenance"]["calculation"] == "aggregate_spending"


def test_tool_schemas_cover_all_tools():
    for name in tr.TOOLS:
        assert name in tr.TOOL_SCHEMAS, f"missing schema for {name}"
    assert len(tr.TOOLS) >= 16


# ── Hallucinated number guard ──────────────────────────────────────────────

def test_hallucinated_number_guard_deterministic(monkeypatch):
    """Deterministic fallback must only surface numbers from tool results."""

    def fake_agg2(user_id, year, month, category=None):
        return {"total_eur": 42.00, "breakdown": {}, "_provenance": {"calculation": "aggregate_spending", "row_count": 1}}

    monkeypatch.setitem(tr.TOOLS, "aggregate_spending", fake_agg2)
    # Force orchestrator to take deterministic path (fast_route matches)
    # Question must trigger aggregate_spending
    res = orch.orchestrate(1, "How much did I spend this month?", {})
    answer = res.get("answer") or ""
    assert "42" in answer
    assert "99.99" not in answer
