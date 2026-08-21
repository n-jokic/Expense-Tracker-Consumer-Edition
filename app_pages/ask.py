"""
Ask your data: chat with your own financials via the bounded advisor.

Privacy: the model only receives sanitized numeric aggregates and recent
descriptions (stripped/capped), never credentials; local provider stays offline.
All numbers come from canonical services (services/finance_queries) with
provenance — no LLM arithmetic.
"""

import os as _os

import streamlit as st

import llm

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

# ── Chat history ───────────────────────────────────────────────────────────
for msg in st.session_state.ask_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

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
    st.button(
        "Confirm change",
        disabled=True,
        help="Confirmation would call budget_commands.set_budget — not yet wired. The model never auto-executes mutations.",
        key="ask_confirm_disabled",
    )
    st.caption("The advisor is read-only. Confirming would apply the change via a command service (one transaction, one audit record).")
    if st.button("Dismiss proposal", key="ask_dismiss_proposal"):
        st.session_state.pop("_last_proposal", None)
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
