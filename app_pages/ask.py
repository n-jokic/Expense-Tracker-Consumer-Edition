"""
Ask your data: chat with your own financials via the bounded advisor.

Privacy: the model only receives sanitized numeric aggregates and recent
descriptions (stripped/capped), never credentials; local provider stays offline.
All numbers come from canonical services (services/finance_queries) with
provenance — no LLM arithmetic.
"""

import json
import os as _os

import streamlit as st

import llm
from ui.styles import CHART_COLORS

user_id = st.session_state.user_id
settings = st.session_state.settings

st.title(":material/forum: Ask your data")
st.caption(
    "Chat with your own financial data. Answers are computed by canonical "
    "finance services (Streamlit + MCP + AI share the same code) and every "
    "number carries provenance — nothing is guessed."
)

provider = llm.resolve_provider(settings)

# ── Provider status line ───────────────────────────────────────────────────
if provider == "local":
    path = str(settings.get("ai_local_model") or "")
    label = _os.path.basename(path) or path or "model"
    ready, diag = llm.local_runtime_status(settings)
    if ready:
        st.caption(f"Provider: **local Gemma** — :green-badge[Local Gemma ready] — {label}")
    elif "does not exist" in diag:
        st.caption(f"Provider: **local Gemma** — :red-badge[model file missing] — {diag}")
    else:
        st.caption(f"Provider: **local Gemma** — :red-badge[runtime missing] — {diag}")
elif provider == "api":
    st.caption(
        f"Provider: **API** — :green-badge[API ready] — "
        f"{settings.get('ai_api_model') or 'configured model'}"
    )

if provider == "none":
    st.info(
        "No AI provider is configured. Common questions still work using "
        "deterministic finance services; configure an AI provider in "
        "**Settings → Notifications → AI assistant** for complex questions.",
        icon=":material/smart_toy:",
    )

if "ask_history" not in st.session_state:
    st.session_state.ask_history = []

# ── Suggested questions (empty chat only) ──────────────────────────────────
SUGGESTIONS = [
    "How much did I spend this month?",
    "What was my biggest expense category?",
    "How does this month compare to last month?",
    "How much fun money do I have left?",
    "What did I spend at the grocery store?",
]

if not st.session_state.ask_history:
    picked = st.pills("Try asking", SUGGESTIONS, label_visibility="collapsed", key="ask_pills")
    if picked:
        st.session_state.ask_history.append({"role": "user", "content": picked})
        st.session_state.ask_pending = picked
        st.rerun()

# ── Inline chart rendering (AI-04) ────────────────────────────────────────────

def _render_chart_from_result(result_dict):
    """AI-04: render a validated chart from a single tool-call result dict.

    Uses the same validate_chart_spec path as the Sources expander so the
    chart data is always the canonical tool rows — the model never supplies
    numbers. Invalid/missing specs render nothing extra (graceful degradation)."""
    if not isinstance(result_dict, dict):
        return
    raw_spec = result_dict.get("_chart")
    if not isinstance(raw_spec, dict):
        return
    try:
        from ai.charts import validate_chart_spec
        spec = validate_chart_spec(
            raw_spec, (result_dict.get("series") or []))
        if spec:
            import pandas as pd
            import plotly.express as px
            cdf = pd.DataFrame(spec["data"])
            if spec["type"] == "line":
                fig = px.line(cdf, x=spec["x"], y=spec["y"], markers=True,
                              color_discrete_sequence=CHART_COLORS)
            elif spec["type"] == "bar":
                fig = px.bar(cdf, x=spec["x"], y=spec["y"],
                             color_discrete_sequence=CHART_COLORS)
            else:
                fig = px.pie(cdf, names=spec["x"], values=spec["y"],
                             color_discrete_sequence=CHART_COLORS)
            fig.update_layout(
                title=spec["title"] or None,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")
    except Exception:
        pass  # never let a bad spec break the page


# ── Chat history ───────────────────────────────────────────────────────────
for i, msg in enumerate(st.session_state.ask_history):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # AI-04: render the chart inline below the assistant answer when the
        # most recent tool-call results carry a validated _chart spec.
        if msg["role"] == "assistant" and i == len(st.session_state.ask_history) - 1:
            for tc in st.session_state.get("_last_tool_calls", []):
                _render_chart_from_result(tc.get("result") or {})

# ── Provenance expander for last answer ────────────────────────────────────
_last_calls = st.session_state.get("_last_tool_calls") or []
if _last_calls:
    with st.expander("Sources — how this answer was computed", expanded=False):
        for tc in _last_calls:
            tool = tc.get("tool", "?")
            args = tc.get("arguments", {})
            prov = (tc.get("result") or {}).get("_provenance", {})
            err = tc.get("error")
            st.markdown(f"**{tool}** `args={args}`")
            if err:
                st.caption(f"Error: {err}")
            else:
                if prov:
                    period = prov.get("period")
                    prev = prov.get("previous_period")
                    row_count = prov.get("row_count", "?")
                    calc = prov.get("calculation", tool)
                    filters = prov.get("filters", {})
                    st.caption(
                        f"Calculation: `{calc}` · Rows: {row_count}"
                        + (f" · Period: {period}" if period else "")
                        + (f" · Previous: {prev}" if prev else "")
                        + (f" · Filters: {filters}" if filters else "")
                        + f" · Basis: {prov.get('currency_basis', 'EUR')}"
                    )
                source_rows = (tc.get("result") or {}).get("expenses")
                if isinstance(source_rows, list) and source_rows:
                    with st.expander("Show source transactions", expanded=False):
                        st.dataframe(source_rows, use_container_width=True, hide_index=True)
                # brief result preview (no raw row dump beyond cap)
                preview = str(tc.get("result", {}))
                if len(preview) > 900:
                    preview = preview[:900] + "…"
                st.code(preview, language="json")
            st.divider()

# ── Mutation proposal (A7) ─────────────────────────────────────────────────
_last_proposal = st.session_state.get("_last_proposal")
if _last_proposal:
    st.warning(_last_proposal.get("message", "Proposed change requires confirmation."), icon=":material/warning:")
    # L3: confirmation goes through ONE audited command service call. The
    # stored proposal is popped BEFORE applying so a double-click cannot
    # apply twice; commands.set_budget re-validates every field server-side.
    if st.button("Confirm change", type="primary", icon=":material/check:",
                 key="ask_confirm_apply"):
        _p = st.session_state.pop("_last_proposal", None) or {}
        try:
            from services.commands import CommandError, set_budget

            res = set_budget(user_id,
                             category=str(_p.get("category") or ""),
                             amount_eur=float(_p.get("amount_eur") or 0),
                             year=_p.get("year"), month=_p.get("month"))
        except (CommandError, TypeError, ValueError) as exc:
            st.error(f"Proposal rejected: {exc}", icon=":material/block:")
        else:
            if res.changed:
                import queries as _q

                _q.bump_db_version()
                st.toast("Budget updated.", icon=":material/check_circle:")
                st.rerun()
    if st.button("Dismiss proposal", key="ask_dismiss_proposal"):
        st.session_state.pop("_last_proposal", None)
        st.rerun()
    st.caption("Proposals are never auto-executed — confirming calls one "
               "audited command (single transaction, single audit record).")

# ── Agent mutation confirm card (#26 E4) ───────────────────────────────────
_mc = st.session_state.get("_last_mutation_confirm")
if _mc:
    st.warning("The assistant wants to book a change — review before "
               "confirming.", icon=":material/warning:")
    st.code(json.dumps(_mc.get("preview") or {}, default=str),
            language="json")

    def _run_agent_command(command: str, args: dict):
        from services import commands as C
        from services.commands import CommandError

        fn = getattr(C, str(command), None)
        if fn is None or str(command) not in getattr(
                C, "UNDO_COMMANDS", {}) and not command.startswith((
                    "add_", "update_", "delete_", "link_", "unlink_")):
            raise CommandError(f"Unknown agent command {command!r}.")
        return fn(user_id, **dict(args or {}))

    c_yes, c_no = st.columns(2)
    if c_yes.button("Confirm booking", type="primary",
                    icon=":material/check:", key="ask_confirm_mutation"):
        _p = st.session_state.pop("_last_mutation_confirm", None) or {}
        try:
            _res = _run_agent_command(str(_p.get("command")),
                                      dict(_p.get("args") or {}))
        except Exception as exc:
            st.error(f"Rejected: {exc}", icon=":material/block:")
        else:
            if getattr(_res, "changed", False):
                import queries as _q

                _q.bump_db_version()
                st.toast("Booked.", icon=":material/check_circle:")
            st.rerun()
    if c_no.button("Dismiss", key="ask_dismiss_mutation"):
        st.session_state.pop("_last_mutation_confirm", None)
        st.rerun()

# ── Undo / Redo cards (#26 E4) ─────────────────────────────────────────────
_offers = st.session_state.get("_undo_offers") or []
if _offers:
    from services.undo import execute_undo

    for _i, _off in enumerate(_offers[:3]):
        _desc = _off.get("undo_description") or _off.get("tool") or "change"
        _box = st.container(border=True)
        with _box:
            st.markdown(f"↩️ **{_desc}**")
            if st.button("Undo", key=f"ask_undo_{_off.get('token_id')}",
                         icon=":material/undo:"):
                _out = execute_undo(str(_off.get("token_id")))
                st.session_state.pop("_undo_offers", None)
                if _out.ok:
                    if _out.changed:
                        import queries as _q

                        _q.bump_db_version()
                        st.session_state["_last_undone"] = {
                            "command": _off.get("tool"),
                            "args": _off.get("stored") or {},
                            "description": _desc,
                        }
                    st.toast(_out.message, icon=":material/undo:")
                else:
                    st.error(_out.message, icon=":material/block:")
                st.rerun()

_undone = st.session_state.get("_last_undone")
if _undone:
    st.info(f"Undone — {_undone.get('description')}.",
            icon=":material/undo:")
    if st.button("Redo", key="ask_redo_mutation",
                 icon=":material/redo:"):
        st.session_state.pop("_last_undone", None)
        _fwd = {"add_expense": "add_expense",
                "add_income": "add_income",
                "add_recurring_template": "add_recurring_template"}
        _cmd = _fwd.get(str(_undone.get("command")))
        if _cmd:
            try:
                _args = dict(_undone.get("args") or {})
                _args.pop("undo_token", None)
                _res2 = _run_agent_command(_cmd, _args)
                if getattr(_res2, "changed", False):
                    import queries as _q

                    _q.bump_db_version()
                    st.toast("Re-applied.", icon=":material/redo:")
            except Exception as exc:
                st.error(f"Redo failed: {exc}", icon=":material/block:")
        st.rerun()

# ── Input ──────────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask about your finances…", submit_mode="disable")
if prompt and prompt.strip():
    st.session_state.ask_history.append({"role": "user", "content": prompt.strip()})
    st.session_state.ask_pending = prompt.strip()
    st.rerun()

if st.session_state.pop("ask_pending", None):
    question = st.session_state.ask_history[-1]["content"]
    prior = st.session_state.ask_history[:-1]
    with st.spinner("Thinking…"):
        from ai.orchestrator import orchestrate

        result = orchestrate(user_id, question, settings, history=prior)

    proposal = result.get("proposal")
    if proposal:
        st.session_state["_last_proposal"] = proposal
        st.session_state["_last_tool_calls"] = []
        st.session_state.ask_history.append(
            {
                "role": "assistant",
                "content": f"⚠️ {proposal.get('message', 'Proposed change requires confirmation.')}\n\n_The model never auto-executes mutations — use Confirm change to apply._",
            }
        )
        st.rerun()

    # ── #26 E4: agent mutation confirm + undo offers ────────────────────
    mc = result.get("mutation_confirm")
    if mc:
        st.session_state["_last_mutation_confirm"] = mc
        prev = mc.get("preview") or {}
        st.session_state.ask_history.append({
            "role": "assistant",
            "content": ("⚠️ Ready to apply: " + str(prev) +
                        "\n\n_Confirm below — nothing is booked silently._"),
        })
        st.rerun()

    mutations = result.get("mutations") or []
    if mutations:
        st.session_state["_undo_offers"] = mutations
        for m in mutations:
            stored = m.get("stored") or {}
            st.session_state.ask_history.append({
                "role": "assistant",
                "content": ("✅ Stored: " + str(stored) +
                            "\n\n_Undo available below._"),
            })
        st.rerun()

    answer = result.get("answer")
    tool_calls = result.get("tool_calls") or []
    if answer:
        st.session_state["_last_tool_calls"] = tool_calls
        st.session_state.pop("_last_proposal", None)
        st.session_state.ask_history.append({"role": "assistant", "content": answer})
        st.rerun()
    else:
        st.session_state["_last_tool_calls"] = tool_calls
        err = result.get("error") or result.get("diagnostic") or "The assistant could not answer this time."
        diag = llm.local_diagnostic()
        hint = diag if diag else "Try a common spending, budget, or recurring-cost question, or configure an AI provider for complex questions."
        if diag and "Settings" not in diag:
            hint += " Configure the provider in Settings → Notifications → AI assistant."
        st.error(f"{err} {hint}")

if st.session_state.ask_history:
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Clear chat", icon=":material/delete_sweep:"):
            st.session_state.ask_history = []
            st.session_state.pop("_last_tool_calls", None)
            st.session_state.pop("_last_proposal", None)
            st.rerun()
    with c2:
        st.caption(
            "Answers are generated from your data by the configured model — "
            "always double-check important numbers against the pages themselves."
        )
