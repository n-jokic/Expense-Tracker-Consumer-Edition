"""
llm.py — optional lightweight LLM (Gemma family) for natural-language helpers.

Two providers, one engine:

  • "local" — llama-cpp-python with a GGUF model. Recommended: Gemma 3 1B
    Q4_K_M (~0.9 GB — fits < 4 GB VRAM with huge headroom, fast on CPU too);
    Gemma 2 2B Q4 (~1.6 GB) is the alternative. See the README for download
    links and install commands (llama-cpp-python is OPTIONAL — imported
    lazily, the rest of the app never needs it).
  • "api"   — any OpenAI-compatible chat-completions endpoint (OpenRouter,
    Groq, Together, ...) with an API key from settings.

Every public call returns None on ANY failure, so callers always fall back
to their existing rule-based text and email sending can never be blocked or
broken by the LLM. Model output is treated as untrusted: callers must
HTML-escape it before embedding.
"""

import logging
import threading

import requests

from crypto import decrypt_str

log = logging.getLogger("llm")

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_API_MODEL = "google/gemma-3-12b-it"

# One local generation at a time; the model is loaded once per path.
_local_lock = threading.Lock()
_local_cache: tuple = (None, None)  # (model_path, llama instance)


# ── Provider resolution ───────────────────────────────────────────────────────

def resolve_provider(settings: dict) -> str:
    """'none' | 'local' | 'api' based on the stored AI settings."""
    provider = str(settings.get("ai_provider") or "none").strip().lower()
    if provider == "local" and not str(settings.get("ai_local_model") or "").strip():
        return "none"
    if provider == "api" and not decrypt_str(settings.get("ai_api_key_enc") or ""):
        return "none"
    return provider if provider in ("local", "api") else "none"


# ── Local (llama-cpp) ─────────────────────────────────────────────────────────

def _get_local_model(settings: dict):
    """Load (once) and return the llama-cpp model for the configured path."""
    global _local_cache
    path = str(settings.get("ai_local_model") or "").strip()
    if not path:
        return None
    if _local_cache[0] == path and _local_cache[1] is not None:
        return _local_cache[1]
    try:
        from llama_cpp import Llama  # optional dependency, imported lazily
        gpu_layers = int(settings.get("ai_local_gpu_layers") or -1)
        model = Llama(model_path=path, n_ctx=2048, n_gpu_layers=gpu_layers,
                      verbose=False)
    except Exception as e:
        log.warning("could not load local LLM model %r: %s", path, e)
        return None
    _local_cache = (path, model)
    log.info("loaded local LLM model %s", path)
    return model


def _local_chat(settings: dict, system: str, user: str, max_tokens: int) -> str | None:
    with _local_lock:
        model = _get_local_model(settings)
        if model is None:
            return None
        try:
            out = model.create_chat_completion(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=int(max_tokens), temperature=0.7, top_p=0.9,
            )
            text = out["choices"][0]["message"]["content"]
            return text.strip() or None
        except Exception as e:
            log.warning("local LLM generation failed: %s", e)
            return None


# ── External OpenAI-compatible API ────────────────────────────────────────────

def _api_chat(settings: dict, system: str, user: str, max_tokens: int) -> str | None:
    key = decrypt_str(settings.get("ai_api_key_enc") or "")
    if not key:
        return None
    base = str(settings.get("ai_api_base") or DEFAULT_API_BASE).rstrip("/")
    model_name = str(settings.get("ai_api_model") or DEFAULT_API_MODEL)
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": model_name,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "max_tokens": int(max_tokens), "temperature": 0.7},
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return text.strip() or None
    except Exception as e:
        # Never echo the key: the exception may carry the request URL only.
        log.warning("LLM API request failed (%s): %s", type(e).__name__, e)
        return None


# ── Public generators ─────────────────────────────────────────────────────────

def _generate(settings: dict, system: str, user: str, max_tokens: int = 256) -> str | None:
    provider = resolve_provider(settings)
    if provider == "local":
        return _local_chat(settings, system, user, max_tokens)
    if provider == "api":
        return _api_chat(settings, system, user, max_tokens)
    return None


def _sanitize_stat(value) -> str:
    """Make a stat value safe for prompt embedding: truncate and strip
    newlines so stored data (which sync-pushed rows could theoretically
    make hostile) cannot inject instructions into the prompt."""
    s = str(value).replace("\r", " ").replace("\n", " ")
    return s[:100]


_SUMMARY_SYSTEM = (
    "You write ONE short paragraph for the user's own weekly spending "
    "summary email. Use ONLY the numbers provided. Do not give financial "
    "advice, do not invent data, do not use markdown or emoji. Plain text, "
    "2-4 sentences, second person."
)


def generate_summary(stats: dict, settings: dict) -> str | None:
    """A plain-text paragraph summarizing the week's spending.

    stats keys (all numeric/EUR, pre-formatted by the caller):
    total_eur, prev_week_eur, top_categories (list of "name — amount"),
    fun_remaining (optional).
    """
    if resolve_provider(settings) == "none":
        return None
    lines = [f"Total spent this week: {_sanitize_stat(stats.get('total_eur', 0))} EUR"]
    if stats.get("prev_week_eur") is not None:
        lines.append(f"Previous week total: {_sanitize_stat(stats['prev_week_eur'])} EUR")
    top = stats.get("top_categories") or []
    if top:
        lines.append("Top categories: " + "; ".join(_sanitize_stat(t) for t in top))
    if stats.get("fun_remaining") is not None:
        lines.append(f"Fun-money budget remaining this month: {_sanitize_stat(stats['fun_remaining'])} EUR")
    user = "\n".join(lines) + "\n\nWrite the summary now."
    return _generate(settings, _SUMMARY_SYSTEM, user)


_NARRATIVE_SYSTEM = (
    "You describe the user's own spending this month in plain language. "
    "Use ONLY the numbers provided. No financial advice, no invented data, "
    "no markdown. 2-4 sentences, second person, neutral tone."
)


def generate_narrative(stats: dict, settings: dict) -> str | None:
    """A plain-text narrative for the Insights page.

    stats keys: spent_eur, prev_spent_eur, change_pct, top_category,
    unusual (list of descriptions), budget_remaining (optional)."""
    if resolve_provider(settings) == "none":
        return None
    lines = [f"Spent this month: {_sanitize_stat(stats.get('spent_eur', 0))} EUR",
             f"Previous month: {_sanitize_stat(stats.get('prev_spent_eur', 0))} EUR",
             f"Month-over-month change: {_sanitize_stat(stats.get('change_pct', 0))} percent"]
    if stats.get("top_category"):
        lines.append(f"Top category: {_sanitize_stat(stats['top_category'])}")
    unusual = stats.get("unusual") or []
    if unusual:
        lines.append("Unusually large expenses: "
                     + "; ".join(_sanitize_stat(u) for u in unusual))
    if stats.get("budget_remaining") is not None:
        lines.append(f"Budget remaining: {_sanitize_stat(stats['budget_remaining'])} EUR")
    user = "\n".join(lines) + "\n\nWrite the narrative now."
    return _generate(settings, _NARRATIVE_SYSTEM, user)
