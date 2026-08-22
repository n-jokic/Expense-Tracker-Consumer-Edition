"""research.md L1 — planner prompt carries a date anchor and tool schemas."""

from datetime import date

from ai import prompts as P


def test_tool_reference_lists_every_registry_tool_with_args():
    ref = P.planner_tool_reference()
    assert "aggregate_spending(year, month" in ref
    assert "compare_periods(start_a, end_a, start_b, end_b" in ref
    assert "savings_status()" in ref  # no-arg tool renders bare
    assert "optional:" in ref


def test_planner_user_prompt_has_date_question_schemas():
    out = P.planner_user_prompt(
        "How much did I spend this month?", date(2026, 8, 21),
        history_block="CHAT HISTORY:\nuser: hi\n",
        prior_results="PRIOR TOOL RESULTS:\n- x() -> {}")
    assert out.startswith("Today is 2026-08-21.")
    assert "QUESTION: How much did I spend this month?" in out
    assert "TOOL ARGUMENT SCHEMAS:" in out
    assert "PRIOR TOOL RESULTS:" in out
    assert '"__answer__"' in out


def test_planner_user_prompt_minimal():
    out = P.planner_user_prompt("q", date(2026, 1, 2))
    assert out.startswith("Today is 2026-01-02.")
    assert "QUESTION: q" in out
