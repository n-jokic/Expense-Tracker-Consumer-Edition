"""
Ask your data: chat with your own financials through the optional AI
assistant (local Gemma or an API key — see Settings → Notifications).

Privacy note honored by design: the model only ever receives a sanitized
snapshot of NUMERIC aggregates (plus your recent transaction descriptions,
stripped and capped), never credentials, and — with the local provider —
nothing leaves this machine at all.
"""

import os as _os

import streamlit as st

import llm

user_id  = st.session_state.user_id
settings = st.session_state.settings

st.title(":material/forum: Ask your data")
st.caption("Chat with your own financial data. The assistant sees only a "
           "sanitized snapshot of your numbers — nothing else — and every "
           "answer is computed from your data, never guessed.")

provider = llm.resolve_provider(settings)

# ── Provider status line ───────────────────────────────────────────────────────
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
    st.caption(f"Provider: **API** — :green-badge[API ready] — "
               f"{settings.get('ai_api_model') or 'configured model'}")

if provider == "none":
    st.info("The AI assistant is not configured yet. Set it up in "
            "**Settings → Notifications → AI assistant** (a local Gemma model "
            "or an API key) — the README has the download steps. "
            "Until then, the rule-based Insights page covers the same ground.",
            icon=":material/smart_toy:")
    st.stop()

if "ask_history" not in st.session_state:
    st.session_state.ask_history = []

# ── Suggested questions (show only on an empty chat) ───────────────────────────
SUGGESTIONS = [
    "How much did I spend this month?",
    "What was my biggest expense category?",
    "How does this month compare to last month?",
    "How much fun money do I have left?",
    "What did I spend at the grocery store?",
]

if not st.session_state.ask_history:
    picked = st.pills("Try asking", SUGGESTIONS, label_visibility="collapsed",
                      key="ask_pills")
    if picked:
        st.session_state.ask_history.append({"role": "user", "content": picked})
        st.session_state.ask_pending = picked
        st.rerun()

# ── Chat history ──────────────────────────────────────────────────────────────
for msg in st.session_state.ask_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# submit_mode="disable" locks the input while a long local generation runs,
# so it can't be interrupted mid-run.
prompt = st.chat_input("Ask about your finances…", submit_mode="disable")
if prompt and prompt.strip():
    st.session_state.ask_history.append({"role": "user", "content": prompt.strip()})
    st.session_state.ask_pending = prompt.strip()
    st.rerun()

if st.session_state.pop("ask_pending", None):
    question = st.session_state.ask_history[-1]["content"]
    # Prior turns give follow-up questions context ("and what about
    # groceries?"); the pending question itself is NOT re-sent as history.
    prior = st.session_state.ask_history[:-1]
    with st.spinner("Thinking…"):
        answer = llm.answer_query(user_id, question, settings, history=prior)
    if answer:
        st.session_state.ask_history.append({"role": "assistant", "content": answer})
        st.rerun()
    else:
        # Keep failures OUT of the transcript: a failed turn must not be
        # stored as an assistant message (it would otherwise be fed back
        # into later prompts as "CHAT SO FAR" context).
        diag = (llm.local_diagnostic() or
                "I couldn't reach the AI assistant (model not loaded or the "
                "API call failed).")
        st.error(f"The assistant could not answer this time. {diag} "
                 "Check the provider in **Settings → Notifications → AI assistant**.")

if st.session_state.ask_history:
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Clear chat", icon=":material/delete_sweep:"):
            st.session_state.ask_history = []
            st.rerun()
    with c2:
        st.caption("Answers are generated from your data by the configured "
                   "model — always double-check important numbers against the "
                   "pages themselves.")
