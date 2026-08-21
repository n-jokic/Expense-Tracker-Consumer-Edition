"""
ai/orchestrator.py — bounded orchestrator loop (Phase 3 A4/A5/A6/A7).

Flow:
  User question
    → safety sanitization
    → mutation proposal? (return proposal, no mutation)
    → deterministic fast-route?  YES → execute 1 tool with inferred args
                                NO  → planner loop (≤ MAX_TOOL_CALLS)
         → schema validation → execute read-only tool → loop
    → answer composer (LLM if available, else deterministic template)
    → provenance attached

No unconstrained ReAct. No direct SQL. No finance arithmetic in ai/.
All numbers come from services/finance_queries (via tool_registry).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from ai.router import (
    MAX_TOOL_CALLS,
    MAX_RESULT_ROWS,
    fast_route,
    parse_local_tool_json,
    validate_tool_call,
    infer_deterministic_args,
)
from ai.schemas import AdvisorToolCall, AdvisorResponse
from ai import prompts as P

log = logging.getLogger("ai.orchestrator")


def _get_provider(settings: dict):
    """Return provider instance or None if not configured."""
    try:
        from llm import resolve_provider

        provider = resolve_provider(settings)
        if provider == "local":
            from ai.providers.llama_cpp import LlamaCppProvider

            return LlamaCppProvider(settings)
        if provider == "api":
            from ai.providers.openai_compatible import OpenAICompatibleProvider

            return OpenAICompatibleProvider(settings)
    except Exception as e:
        log.warning("provider resolution failed: %s", e)
    return None


def _truncate_result(result: dict) -> dict:
    """Enforce MAX_RESULT_ROWS on any list fields in tool result."""
    if not isinstance(result, dict):
        return result
    for k, v in list(result.items()):
        if isinstance(v, list) and len(v) > MAX_RESULT_ROWS:
            result[k] = v[:MAX_RESULT_ROWS]
            prov = result.get("_provenance")
            if isinstance(prov, dict):
                prov["truncated"] = True
            result["_truncated"] = True
    return result


def _execute_tool(tool: str, arguments: dict, user_id: int) -> tuple[dict | None, str | None]:
    """Execute a read-only finance tool. Returns (result, error)."""
    try:
        from ai.tool_registry import TOOLS
        from ai.safety import is_read_only_tool

        if not is_read_only_tool(tool):
            return None, f"tool not allowed: {tool}"
        fn = TOOLS.get(tool)
        if fn is None:
            return None, f"unknown tool: {tool}"
        # Cap list-size args
        if "limit" in arguments:
            try:
                arguments["limit"] = max(1, min(int(arguments["limit"]), MAX_RESULT_ROWS))
            except Exception:
                arguments["limit"] = 20
        if "n" in arguments:
            try:
                arguments["n"] = max(1, min(int(arguments["n"]), 20))
            except Exception:
                arguments["n"] = 5
        result = fn(user_id=user_id, **arguments)
        if not isinstance(result, dict):
            result = {"result": result, "_provenance": {"calculation": tool}}
        result = _truncate_result(result)
        # Ensure _provenance exists
        if "_provenance" not in result:
            result["_provenance"] = {"calculation": tool, "row_count": 0}
        return result, None
    except TypeError as e:
        return None, f"argument error for {tool}: {e}"
    except ValueError as e:
        return None, str(e)
    except Exception as e:
        log.warning("tool %s failed: %s", tool, e)
        return None, str(e)


def _deterministic_answer(tool: str, result: dict) -> str:
    """Fallback plain-text answer when LLM unavailable — no invented numbers."""
    prov = result.get("_provenance", {})
    calc = prov.get("calculation") or tool
    # Build summary from result keys
    if tool in ("aggregate_spending",) or "total_eur" in result:
        total = result.get("total_eur")
        prev = result.get("previous_eur")
        if total is not None and prev is not None:
            diff = result.get("difference_eur", 0)
            pct = result.get("change_pct")
            pct_s = f" ({pct:.1f}%)" if pct is not None else ""
            return f"Total €{total:.2f} vs €{prev:.2f} previously — difference €{diff:.2f}{pct_s}. Based on {prov.get('row_count', '?')} transactions [{calc}]."
        if total is not None:
            return f"Total €{total:.2f} [{calc}]. Breakdown: {result.get('breakdown', result)}"
    if tool == "budget_status" or "budgeted_eur" in str(result):
        return f"Budget status: {json.dumps(result, default=str)[:600]} [{calc}]"
    if tool == "recurring_costs":
        return f"Recurring bills total €{result.get('monthly_total_eur', 0):.2f}/month [{calc}]."
    if tool == "savings_status":
        return f"Savings: {json.dumps(result, default=str)[:600]} [{calc}]"
    if tool == "debt_summary":
        return f"Debt: €{result.get('total_debt_eur', 0):.2f} total, €{result.get('monthly_payments_eur', 0):.2f}/month [{calc}]"
    if tool == "forecast":
        tot = result.get("total")
        if tot is None:
            return f"Forecast: not enough history ({result.get('history_months', 0)} months) — using fallback [{calc}]."
        return f"Forecast next month: €{tot:.2f} (range €{result.get('lower', 0):.2f}–€{result.get('upper', 0):.2f}) [{calc}]."
    # Generic
    summary = json.dumps(result, default=str)[:700]
    return f"Based on your data ({calc}): {summary}"


def _compose_answer(
    question: str,
    tool_calls: list[AdvisorToolCall],
    settings: dict,
) -> tuple[str | None, str]:
    """Try LLM answer composer; fall back to deterministic."""
    if not tool_calls:
        return None, "no tool results to compose answer from"
    # Build tool results block for prompt — aggregate, no raw row dump beyond caps
    blocks = []
    for tc in tool_calls:
        blocks.append(f"[{tc.tool}] arguments={json.dumps(tc.arguments, default=str)} result={json.dumps(tc.result, default=str)[:2000]}")
    tool_block = "\n".join(blocks)

    provider = _get_provider(settings)
    if provider is not None:
        try:
            from ai.providers.base import GenerationRequest

            system = P.ADVISOR_SYSTEM
            user = f"QUESTION:\n{question}\n\nTOOL RESULTS:\n{tool_block}\n\nAnswer now."
            req = GenerationRequest(system=system, user=user, max_tokens=300)
            res = provider.generate(req)
            if res.text and res.text.strip():
                # Safety: ensure response doesn't contain hallucinated SQL patterns
                from ai.safety import validate_no_sql

                ok, _ = validate_no_sql(res.text)
                if ok:
                    return res.text.strip(), ""
        except Exception as e:
            log.warning("answer composer failed: %s", e)
    # Deterministic fallback: join per-tool deterministic answers
    parts = []
    for tc in tool_calls:
        if tc.error:
            parts.append(f"[{tc.tool}] error: {tc.error}")
        elif tc.result:
            parts.append(_deterministic_answer(tc.tool, tc.result))
    return "\n".join(parts) if parts else None, "deterministic fallback"


def orchestrate(
    user_id: int,
    question: str,
    settings: dict,
    history: list | None = None,
) -> dict:
    """Run one advisor turn. Returns {answer, tool_calls, error, diagnostic, proposal}."""
    from ai.safety import sanitize_question, check_mutation_proposal

    q_raw = question or ""
    q = sanitize_question(q_raw)
    if not q or not q.strip():
        return {"answer": None, "error": "Empty question.", "tool_calls": []}

    # A7 — mutation proposal: never executed by the model
    proposal = check_mutation_proposal(q)
    if proposal is not None:
        return {
            "answer": proposal.get("message", "Proposed change requires confirmation."),
            "tool_calls": [],
            "proposal": proposal,
            "error": None,
        }

    today = date.today()
    tool_calls: list[AdvisorToolCall] = []

    # ── Deterministic fast-route ───────────────────────────────────────
    fast = fast_route(q)
    if fast is not None:
        try:
            from ai.tool_registry import TOOLS

            if fast in TOOLS:
                args = infer_deterministic_args(fast, q, today)
                # Validate before execution
                ok, err = validate_tool_call(fast, args)
                if not ok:
                    return {"answer": None, "error": err, "tool_calls": []}
                result, exec_err = _execute_tool(fast, args, user_id)
                tc = AdvisorToolCall(tool=fast, arguments=args, result=result or {}, error=exec_err)
                tool_calls.append(tc)
                if exec_err:
                    return {"answer": None, "error": exec_err, "tool_calls": [tc.__dict__]}
                answer, diag = _compose_answer(q, tool_calls, settings)
                return {
                    "answer": answer,
                    "tool_calls": [tc.__dict__ for tc in tool_calls],
                    "error": None,
                    "diagnostic": diag,
                }
        except Exception as e:
            log.warning("fast-route failed: %s", e)
            # fall through to planner

    # ── Planner loop (LLM) ─────────────────────────────────────────────
    provider = _get_provider(settings)
    if provider is None:
        # No LLM — if no fast-route matched, we cannot plan. Try a heuristic
        # fallback: if question looks like a finance question, try aggregate_spending
        # for current month as a graceful degradation.
        if not tool_calls:
            return {
                "answer": None,
                "error": "No deterministic route matched and no AI provider is configured. Try: How much did I spend this month? Or configure a provider in Settings → AI assistant.",
                "tool_calls": [],
                "diagnostic": "no provider, no fast-route",
            }

    # Planner iterations bounded by MAX_TOOL_CALLS
    for iteration in range(MAX_TOOL_CALLS):
        # Build planner prompt with context so far
        prior_results = ""
        if tool_calls:
            prior_results = "\nPRIOR TOOL RESULTS:\n" + "\n".join(
                f"- {tc.tool}({json.dumps(tc.arguments, default=str)}) -> {json.dumps(tc.result, default=str)[:800]}"
                for tc in tool_calls
            )
        hist_block = ""
        if history:
            turns = []
            for h in history[-4:]:
                if isinstance(h, dict):
                    role = str(h.get("role", "user"))
                    content = str(h.get("content", ""))[:200].replace("\n", " ")
                    turns.append(f"{role}: {content}")
            if turns:
                hist_block = "CHAT HISTORY:\n" + "\n".join(turns) + "\n"

        try:
            from ai.providers.base import GenerationRequest
            from ai.router import parse_local_tool_json

            planner_user = (
                f"{hist_block}QUESTION: {q}\n"
                f"{prior_results}\n"
                "Output ONLY the next JSON tool call {\"tool\": \"...\", \"arguments\": {...}} "
                "or, if you have enough information to answer, output {\"tool\": \"__answer__\"}."
            )
            req = GenerationRequest(system=P.PLANNER_SYSTEM, user=planner_user, max_tokens=256)
            res = provider.generate(req)
            text = (res.text or "").strip()
            if not text:
                break

            # Allow planner to signal it is ready to answer
            obj_probe = None
            try:
                import json as _j

                obj_probe = _j.loads(text.strip())
                if isinstance(obj_probe, dict) and obj_probe.get("tool") == "__answer__":
                    break
            except Exception:
                pass

            parsed = parse_local_tool_json(text)
            if parsed is None:
                # One repair attempt
                repair_req = GenerationRequest(
                    system=P.PLANNER_SYSTEM,
                    user=P.REPAIR_INSTRUCTION + f"\nYour previous output:\n{text[:500]}",
                    max_tokens=256,
                )
                repair_res = provider.generate(repair_req)
                parsed = parse_local_tool_json((repair_res.text or "").strip())
                if parsed is None:
                    log.warning("planner produced invalid JSON twice, stopping")
                    break

            tool = parsed["tool"]
            args = parsed.get("arguments", {})
            ok, err = validate_tool_call(tool, args)
            if not ok:
                # One repair attempt for arg validation too
                log.warning("planner args invalid: %s", err)
                break

            # Deduplicate: don't call same tool with same args twice
            if any(tc.tool == tool and tc.arguments == args for tc in tool_calls):
                break

            result, exec_err = _execute_tool(tool, args, user_id)
            tc = AdvisorToolCall(tool=tool, arguments=args, result=result or {}, error=exec_err)
            tool_calls.append(tc)
            if exec_err:
                # Tool error is feedback to planner — allow one more iteration
                continue
            # Heuristic: after 1-2 successful calls, we have enough to answer.
            # For describe/diagnose we often need 1; for plan/coach maybe 2-3.
            # Let planner decide on next iteration whether to call again.
            if len(tool_calls) >= 2:
                # Peek if planner still wants to call more — we enforce cap via loop
                pass
        except Exception as e:
            log.warning("planner iteration %d failed: %s", iteration, e)
            break

    if not tool_calls:
        return {
            "answer": None,
            "error": "The planner could not produce a valid tool call. Try rephrasing your question.",
            "tool_calls": [],
        }

    answer, diag = _compose_answer(q, tool_calls, settings)
    err = None
    if not answer:
        err = "The assistant could not compose an answer from the tool results."
    return {
        "answer": answer,
        "tool_calls": [tc.__dict__ for tc in tool_calls],
        "error": err,
        "diagnostic": diag,
    }
