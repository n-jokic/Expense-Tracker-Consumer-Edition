"""
Ask your data: chat with your own financials through the optional AI
assistant (local Gemma or an API key — see Settings → Notifications).

Privacy note honored by design: the model only ever receives a sanitized
snapshot of NUMERIC aggregates (plus your recent transaction descriptions,
stripped and capped), never credentials, and — with the local provider —
nothing leaves this machine at all.
"""

import streamlit as st

import llm

user_id  = st.session_state.user_id
settings = st.session_state.settings

st.title(":material/forum: Ask your data")
st.caption("Chat with your own financial data. The assistant sees only a "
           "sanitized snapshot of your numbers — nothing else — and every "
           "answer is computed from your data, never guessed.")

if llm.resolve_provider(settings) == "none":
    st.info("The AI assistant is not configured yet. Set it up in "
            "**Settings → Notifications → AI assistant** (a local Gemma model "
            "or an API key) — the README has the download steps. "
            "Until then, the rule-based Insights page covers the same ground.",
            icon=":material/smart_toy:")
    st.stop()

if "ask_history" not in st.session_state:
    st.session_state.ask_history = []

# ── Suggested questions ───────────────────────────────────────────────────────
with st.expander("Suggested questions", icon=":material/lightbulb:"):
    sugg = st.container(horizontal=True)
    suggestions = [
        "How much did I spend this month?",
        "What was my biggest expense category?",
        "How does this month compare to last month?",
        "How much fun money do I have left?",
        "What did I spend at the grocery store?",
    ]
    for i, s in enumerate(suggestions):
        if sugg.button(s, key=f"ask_sugg_{i}"):
            st.session_state.ask_history.append({"role": "user", "content": s})
            st.session_state.ask_pending = s

# ── Chat history ──────────────────────────────────────────────────────────────
for msg in st.session_state.ask_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask about your finances…")
if prompt and prompt.strip():
    st.session_state.ask_history.append({"role": "user", "content": prompt.strip()})
    st.session_state.ask_pending = prompt.strip()
    st.rerun()

if st.session_state.pop("ask_pending", None):
    question = st.session_state.ask_history[-1]["content"]
    with st.spinner("Thinking…"):
        answer = llm.answer_query(user_id, question, settings)
    fallback = ("I couldn't reach the AI assistant (model not loaded or the "
                "API call failed). Check the provider in Settings → "
                "Notifications → AI assistant.")
    st.session_state.ask_history.append(
        {"role": "assistant", "content": answer or fallback})
    st.rerun()

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
