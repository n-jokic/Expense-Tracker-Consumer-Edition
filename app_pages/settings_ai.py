"""
settings_ai.py — the optional AI-assistant settings UI (Settings → Notifications).

Split out of notifications.py so the email module never imports (or crashes
on) the LLM stack. Rendered from the Settings page right below the email
notification settings.
"""

import logging

import streamlit as st

import queries as q
from crypto import encrypt_str as _encrypt
from llm import (
    DEFAULT_API_BASE, DEFAULT_API_MODEL, find_bundled_model,
    generate_summary, local_diagnostic, local_runtime_status,
)

log = logging.getLogger("llm.settings_ai")


def render_ai_settings(user_id: int, settings: dict) -> None:
    """AI assistant section: provider select, local model path / API fields,
    runtime status, save + one-click test. Keys and the form name are stable
    so session state survives the move out of notifications.py."""
    st.subheader(":material/smart_toy: AI assistant (optional)")
    st.caption("A lightweight local Gemma model (< 4 GB VRAM, via llama.cpp) or "
               "an external API key writes the weekly summary paragraph and the "
               "Insights narrative. Without it, everything falls back to the "
               "built-in templates — see the README for model downloads and setup.")

    @st.fragment
    def _ai_settings_fragment():
        cur_provider = str(settings.get("ai_provider") or "none")
        # Provider selector is OUTSIDE the form so changing it rerenders the
        # provider-specific fields before submit (reactive invariant).
        ai_provider = st.selectbox(
            "Provider",
            ["none", "local", "api"],
            index=["none", "local", "api"].index(cur_provider)
            if cur_provider in ("local", "api") else 0,
            format_func={"none": "Off",
                         "local": "Local Gemma model (llama.cpp)",
                         "api": "External API"}.get,
            key="ai_provider_select")
        # AI-04/#22: which external API family? Each has its own adapter,
        # auth and endpoint; the choice is persisted as the ai_api_kind
        # column (legacy installs were backfilled from the old URL sniff).
        cur_kind = str(settings.get("ai_api_kind") or "").strip().lower()
        ai_api_kind = None
        if ai_provider == "api":
            ai_api_kind = st.selectbox(
                "API family",
                ["openai_compatible", "anthropic", "gemini"],
                index={"anthropic": 1, "gemini": 2}.get(cur_kind, 0),
                format_func={"openai_compatible":
                             "OpenAI-compatible (OpenRouter, OpenAI, …)",
                             "anthropic": "Anthropic Claude (native)",
                             "gemini": "Google Gemini (AI Studio key)"}.get,
                key="ai_api_kind_select",
                help="Gemini uses a Google AI Studio key and its own "
                     "generateContent endpoint; model defaults to "
                     "gemini-2.0-flash when left blank.")
        # Form key includes provider so switching provider reconstructs the form.
        with st.form(f"ai_form_{ai_provider}"):
            ai_model_path = ai_gpu = ai_base = ai_model = ai_key = None
            if ai_provider == "local":
                ai_model_path = st.text_input(
                    "GGUF model file path",
                    value=str(settings.get("ai_local_model") or
                              find_bundled_model() or ""),
                    placeholder=r"C:\models\gemma-3-1b-it-Q4_K_M.gguf",
                    help="Source installs auto-detect data\\models\\google_gemma-3-1b-it-Q4_K_M.gguf "
                         "in the repo folder; the installed app auto-detects "
                         "%LOCALAPPDATA%\\ExpenseTracker\\models\\google_gemma-3-1b-it-Q4_K_M.gguf. "
                         "You can also enter any other GGUF path.")
                # Runtime status indicator: reflects the path as typed (before saving).
                merged = dict(settings)
                merged["ai_provider"] = "local"
                merged["ai_local_model"] = (ai_model_path or "").strip()
                ready, diag = local_runtime_status(merged)
                if ready:
                    st.caption(":green-badge[Runtime ready] — the llama.cpp runtime and the model file "
                               "were found.")
                elif "does not exist" in diag:
                    st.caption(f":red-badge[Model file missing] — {diag}")
                else:
                    st.caption(f":red-badge[Runtime missing] — {diag}")
                ai_gpu = st.number_input(
                    "GPU layers (-1 = all to GPU, 0 = CPU)",
                    value=int(-1 if settings.get("ai_local_gpu_layers") is None
                              else settings["ai_local_gpu_layers"]),
                    min_value=-1, max_value=999, step=1)
            elif ai_provider == "api":
                ai_base = st.text_input("API base URL",
                                        value=str(settings.get("ai_api_base")
                                                  or DEFAULT_API_BASE))
                ai_model = st.text_input("Model name",
                                         value=str(settings.get("ai_api_model")
                                                   or DEFAULT_API_MODEL))
                ai_key = st.text_input("Platform API key", type="password",
                                        placeholder="Leave blank to keep the existing key",
                                        help="A platform API key with API billing — app/browser subscriptions (ChatGPT Plus, Claude Pro, Gemini app) do not include API access.")
                # OCR-02: explicit opt-in gate — never on by default.
                cloud_fb = st.checkbox(
                    "Allow cloud OCR fallback for unreadable receipts "
                    "(sends the photo off-device)",
                    value=bool(settings.get("ocr_cloud_fallback")),
                    key="ocr_cloud_fallback_cb")
            c_save, c_test = st.columns(2)
            with c_save:
                ai_saved = st.form_submit_button("Save AI settings", type="primary",
                                                 icon=":material/save:", width="stretch")
            with c_test:
                ai_test = st.form_submit_button("Test summary",
                                                icon=":material/smart_toy:", width="stretch")
            # AI-04: connection tests disclose exactly what may leave the
            # device — per provider kind, before the user runs the test.
            if ai_provider == "api":
                st.caption(
                    ":material/info: What this test sends to the external "
                    "provider: your question plus sanitized aggregate figures "
                    "(weekly totals, category names). Identifiers, emails, "
                    "local file paths and anything credential-shaped are "
                    "redacted first. Raw transactions never leave the device.")
                # #22 honesty copy: what a platform key is and is not.
                _kind_l = str(ai_api_kind or cur_kind or "").strip().lower()
                _kind_label = {"anthropic": "Anthropic Claude",
                               "gemini": "Google Gemini"}.get(
                    _kind_l, "OpenAI-compatible")
                _tier_note = (
                    "Gemini's free tier via Google AI Studio covers roughly "
                    "1,500 requests/day — plenty for personal use."
                    if _kind_l == "gemini" else
                    "Create the key in the provider's developer console.")
                st.caption(
                    ":material/key: **" + _kind_label + "** needs a *platform API* key "
                    "with API billing — ChatGPT/Claude/Gemini app subscriptions do not "
                    "include API access, and Claude does not allow OAuth logins in "
                    "third-party apps. " + _tier_note)
            elif ai_provider == "local":
                st.caption(
                    ":material/shield: Nothing leaves the device with the "
                    "local model — generation runs entirely on this PC.")

            # We render the submit buttons inside the form but handle their
            # actions here at the fragment level so provider is already the
            # re-rendered value (no same-form dependency).
            # Streamlit sets ai_saved/ai_test on submit; we act after the with.
            if ai_saved:
                # NB: provider-specific fields only exist when their provider is
                # SELECTED at render time. Provider is now outside the form so
                # switching and saving in one action still captures the new branch.
                updates = {"ai_provider": ai_provider}
                if ai_provider == "local" and ai_model_path is not None and ai_gpu is not None:
                    updates.update({"ai_local_model": (ai_model_path or "").strip(),
                                    "ai_local_gpu_layers": int(ai_gpu)})
                elif ai_provider == "api":
                    updates.update({
                        "ai_api_kind": (ai_api_kind or
                                        str(settings.get("ai_api_kind") or
                                            "openai_compatible")).strip(),
                        "ai_api_base": ((ai_base or settings.get("ai_api_base")
                                         or DEFAULT_API_BASE) or "").strip(),
                        "ai_api_model": ((ai_model or settings.get("ai_api_model")
                                          or "") or "").strip(),
                        # OCR-02: persisted opt-in for the (future) vision fallback
                        "ocr_cloud_fallback": bool(cloud_fb),
                    })
                    if ai_key:
                        updates["ai_api_key_enc"] = _encrypt(ai_key)
                try:
                    q.save_settings(user_id, updates)
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    st.success("AI settings saved.", icon=":material/check:")
                    st.rerun()

            if ai_test:
                merged = dict(settings)
                merged["ai_provider"] = ai_provider
                if ai_provider == "local" and ai_model_path is not None and ai_gpu is not None:
                    merged.update({"ai_local_model": ai_model_path.strip(),
                                   "ai_local_gpu_layers": int(ai_gpu)})
                elif ai_provider == "api":
                    if ai_api_kind is not None:
                        merged["ai_api_kind"] = ai_api_kind
                    if ai_base is not None:
                        merged["ai_api_base"] = ai_base.strip()
                    if ai_model is not None:
                        merged["ai_api_model"] = ai_model.strip()
                    if ai_key:
                        merged["ai_api_key_enc"] = _encrypt(ai_key)
                try:
                    with st.spinner("Generating (this can take a few seconds on CPU)…"):
                        out = generate_summary(
                            {"total_eur": 123.45, "prev_week_eur": 98.20,
                             "top_categories": ["Groceries (52.10 EUR)",
                                                "Transport (18.00 EUR)"]},
                            merged)
                except Exception as e:  # pragma: no cover - defensive
                    log.warning("test summary raised: %s", type(e).__name__)
                    st.error("The test summary failed unexpectedly — check the provider, "
                             f"the model path, and the llama.cpp runtime. ({type(e).__name__}: {e})")
                    out = None
                if out:
                    st.success(out, icon=":material/smart_toy:")
                else:
                    st.warning(local_diagnostic() or "No summary generated — check the provider, the model "
                               "path, or the API key, then try again.")

    _ai_settings_fragment()
