"""ai/prompts.py — advisor prompts (Phase 3 A3/A5/A6)."""

# System prompt for final answer composition — the LLM must use ONLY tool numbers.
ADVISOR_SYSTEM = (
    "You are a helpful financial advisor for the user's OWN data. "
    "Use ONLY the numbers in the TOOL RESULTS block — never invent figures, "
    "never guess, never use markdown tables. Answer in 1-4 plain sentences, "
    "second person, neutral tone. If the tool results cannot answer the "
    "question, say exactly that and suggest what other data might help. "
    "Cite provenance when present (period, row count). When a TOOL RESULTS block includes a validated chart spec (_chart), the chart is rendered below the answer automatically as the chart below — never claim you cannot create or show plots or images."
)

# Planner system — constrains local Gemma 1B to output ONLY JSON {tool, arguments}
PLANNER_SYSTEM = (
    "You are a finance tool planner. Output ONLY a single JSON object with "
    "keys 'tool' and 'arguments'. No prose, no markdown, no explanation. "
    "Example: {\"tool\": \"compare_periods\", \"arguments\": {\"start_a\": \"2026-06-01\", \"end_a\": \"2026-06-30\", \"start_b\": \"2025-06-01\", \"end_b\": \"2025-06-30\"}} "
    "Available tools: search_transactions, aggregate_spending, compare_periods, "
    "category_breakdown, merchant_breakdown, budget_status, budget_runway, "
    "cashflow_summary, savings_status, project_savings, debt_summary, "
    "loan_scenario, recurring_costs, subscription_changes, anomalies, forecast, purchase_scenario. "
    "Use the arguments schema exactly. Dates are YYYY-MM-DD. Year/month are integers."
)

ANSWER_SYSTEM = ADVISOR_SYSTEM

# Repair prompt when planner output was invalid
REPAIR_INSTRUCTION = (
    "Your previous output was not valid JSON or used an unknown tool/argument. "
    "Output ONLY a single JSON object {\"tool\": \"...\", \"arguments\": {...}} "
    "with a valid tool name and required arguments. No extra text."
)


def repair_prompt(question: str, previous_output: str,
                  error: str | None = None, schema_text: str = "") -> str:
    """AI-02: full repair context — original question, the target tool's
    schema, the validation error and the previous output — so one bounded
    repair attempt has everything needed to succeed deterministically."""
    parts = [REPAIR_INSTRUCTION]
    if error:
        parts.append(f"Validation error: {error}")
    if schema_text:
        parts.append(f"Argument schema:\n{schema_text}")
    parts.append(f"Original question: {question}")
    parts.append(f"Your previous output:\n{previous_output[:500]}")
    return "\n".join(parts)


def planner_tool_reference() -> str:
    """Compact per-tool argument reference rendered into the planner prompt so
    small models stop guessing argument names."""
    try:
        from ai.tool_registry import TOOL_SCHEMAS
    except Exception:
        return ""
    lines = []
    for name, spec in sorted(TOOL_SCHEMAS.items()):
        req = list(spec.get("required", []))
        opt = list(spec.get("optional", []))
        parts = req + ([f"optional: {', '.join(opt)}"] if opt else [])
        lines.append(f"{name}({', '.join(parts)})" if parts else f"{name}()")
    return "\n".join(lines)


def planner_user_prompt(question: str, today, history_block: str = "",
                        prior_results: str = "") -> str:
    """Full planner user turn: date anchor + question + schemas + prior calls.

    The Today line lets tiny local models resolve "this month" instead of
    guessing a year/month; schemas remove argument-name hallucinations."""
    schema_block = planner_tool_reference()
    parts = [f"Today is {today.isoformat()}."]
    if history_block:
        parts.append(history_block.rstrip())
    parts.append(f"QUESTION: {question}")
    if prior_results:
        parts.append(prior_results.rstrip())
    if schema_block:
        parts.append(f"TOOL ARGUMENT SCHEMAS:\n{schema_block}")
    parts.append(
        'Output ONLY the next JSON tool call {"tool": "...", '
        '"arguments": {...}} or, if you have enough information to answer, '
        'output {"tool": "__answer__"}.'
    )
    return "\n".join(parts)


# Deterministic template when LLM is unavailable or fails
DETERMINISTIC_ANSWER_TEMPLATE = (
    "Based on your data ({calculation}): {summary}"
)

PROMPTS = {
    "advisor_system": ADVISOR_SYSTEM,
    "planner_system": PLANNER_SYSTEM,
    "answer_system": ANSWER_SYSTEM,
    "repair": REPAIR_INSTRUCTION,
}

# Legacy keys for llm.py callers (not used by orchestrator but kept for compat)
LEGACY_SUMMARY_SYSTEM = (
    "You write ONE short paragraph for the user's own weekly spending "
    "summary email. Use ONLY the numbers provided. Do not give financial "
    "advice, do not invent data, do not use markdown or emoji. Plain text, "
    "2-4 sentences, second person."
)
