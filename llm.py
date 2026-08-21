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

AI-01 privacy boundary: ``_api_chat`` is the single point where a request
leaves the machine, and it passes every outbound prompt through
``ai.safety.sanitize_outbound_text`` (credentials, home/workspace paths, and
emails redacted; counts logged at DEBUG, never values). The local path
(``_local_chat``) keeps the user's own context on-device but still strips
credential-shaped strings via ``ai.safety.strip_credentials``.
"""

import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from ai.safety import sanitize_outbound_text, strip_credentials
from crypto import decrypt_str
from app_paths import model_dir

log = logging.getLogger("llm")

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_API_MODEL = "google/gemma-3-12b-it"
DEFAULT_LOCAL_MODEL_FILENAME = "google_gemma-3-1b-it-Q4_K_M.gguf"

# The exact command a source run needs to install the optional llama.cpp
# runtime (Vulkan wheel, pinned) — shown verbatim in UI diagnostics.
LOCAL_RUNTIME_INSTALL_HINT = (
    "The llama.cpp runtime is not installed. Run in the project folder: "
    "`.venv-clean\\Scripts\\python.exe -m pip install --extra-index-url "
    "https://abetlen.github.io/llama-cpp-python/whl/vulkan "
    "llama-cpp-python==0.3.34`"
)


@dataclass
class LocalResult:
    """Outcome of one generation attempt: the text (or None) plus an
    actionable diagnostic for the UI when it failed."""
    text: str | None
    diagnostic: str = ""


# One local generation at a time; the model is loaded once per
# (path, gpu_layers) pair.
_local_lock = threading.Lock()
_local_cache: tuple = ()  # (model_path, gpu_layers, llama instance) or ()
_last_result: LocalResult | None = None  # backing store for local_diagnostic()


# ── Provider resolution ───────────────────────────────────────────────────────

def find_bundled_model() -> str | None:
    """Return the app-local Gemma path when the optional GGUF is present."""
    path = model_dir() / DEFAULT_LOCAL_MODEL_FILENAME
    return str(path) if path.is_file() else None


def resolve_provider(settings: dict) -> str:
    """'none' | 'local' | 'api' based on the stored AI settings."""
    provider = str(settings.get("ai_provider") or "none").strip().lower()
    model_path = str(settings.get("ai_local_model") or "").strip()
    if provider == "local" and not model_path and not find_bundled_model():
        return "none"
    if provider == "api" and not decrypt_str(settings.get("ai_api_key_enc") or ""):
        return "none"
    return provider if provider in ("local", "api") else "none"


# ── Local (llama-cpp) ─────────────────────────────────────────────────────────

def _runtime_missing_diagnostic() -> str:
    """Environment-aware "runtime unavailable" message: a frozen (installed)
    build can only be fixed by reinstalling; a source run gets the exact
    pip command."""
    if getattr(sys, "frozen", False):
        return "The bundled llama.cpp runtime is unavailable. Reinstall Expense Tracker."
    return LOCAL_RUNTIME_INSTALL_HINT


def _get_local_model(settings: dict):
    """Load (once) and return the llama-cpp model for the configured path."""
    global _local_cache, _last_result
    _last_result = LocalResult(None, "")  # clear stale diagnostics (A2.3)
    path = (str(settings.get("ai_local_model") or "").strip()
            or find_bundled_model())
    if not path:
        _last_result = LocalResult(None, "Choose a GGUF model file before testing Local AI.")
        return None
    if not Path(path).is_file():
        _last_result = LocalResult(None, f"GGUF model file does not exist: {path}")
        return None
    try:
        # Non-numeric garbage must not escape the try below as a NameError.
        gpu_layers = int(-1 if settings.get("ai_local_gpu_layers") is None
                         else settings["ai_local_gpu_layers"])
    except (TypeError, ValueError):
        gpu_layers = -1
    if (len(_local_cache) == 3 and _local_cache[0] == path
            and _local_cache[1] == gpu_layers and _local_cache[2] is not None):
        return _local_cache[2]
    try:
        # Optional dependency, imported lazily. Catch Exception (not just
        # ImportError): missing Vulkan/MSVC DLLs raise OSError here and must
        # surface as a diagnostic, never as a crash.
        from llama_cpp import Llama
    except Exception:
        _last_result = LocalResult(None, _runtime_missing_diagnostic())
        log.warning("llama_cpp import failed", exc_info=True)
        return None
    try:
        model = Llama(model_path=path, n_ctx=2048, n_gpu_layers=gpu_layers,
                      verbose=False)
    except Exception as e:
        if gpu_layers != 0:
            try:
                model = Llama(model_path=path, n_ctx=2048, n_gpu_layers=0,
                              verbose=False)
                _last_result = LocalResult(None, (
                    "Vulkan initialization failed; using CPU fallback. "
                    f"Original error: {e}"))
                _local_cache = (path, 0, model)
                log.warning("local LLM Vulkan load failed; using CPU fallback: %s", e)
                return model
            except Exception as cpu_error:
                _last_result = LocalResult(None, f"Could not load this GGUF model: {cpu_error}")
        else:
            _last_result = LocalResult(None, f"Could not load this GGUF model: {e}")
        log.warning("could not load local LLM model %r: %s", path, e)
        return None
    _local_cache = (path, gpu_layers, model)
    log.info("loaded local LLM model %s", path)
    return model


def local_runtime_status(settings: dict) -> tuple[bool, str]:
    """(ready, diagnostic) for the Local provider WITHOUT loading the model:
    ready is True only when the llama_cpp package imports AND the resolved
    GGUF path exists. The diagnostic is an actionable one-liner otherwise
    ("" when ready). Used by the Ask page badge and the Settings indicator."""
    path = (str(settings.get("ai_local_model") or "").strip()
            or find_bundled_model())
    if not path:
        return False, "Choose a GGUF model file before testing Local AI."
    if not Path(path).is_file():
        return False, f"GGUF model file does not exist: {path}"
    try:
        from llama_cpp import Llama  # noqa: F401  — existence check only
    except Exception:
        msg = _runtime_missing_diagnostic()
        global _last_result
        _last_result = LocalResult(None, msg)
        return False, msg
    return True, ""


def local_diagnostic() -> str:
    """Most recent actionable Local AI load/generation status."""
    return _last_result.diagnostic if _last_result else ""


def _local_chat(settings: dict, system: str, user: str, max_tokens: int) -> LocalResult:
    global _last_result
    # Local model = nothing leaves the device, so local context (paths,
    # emails, raw rows) is preserved — but credential-shaped strings are
    # still stripped before they reach any prompt.
    system = strip_credentials(system)
    user = strip_credentials(user)
    with _local_lock:
        model = _get_local_model(settings)
        if model is None:
            return LocalResult(None, local_diagnostic())
        try:
            out = model.create_chat_completion(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=int(max_tokens), temperature=0.7, top_p=0.9,
            )
            text = out["choices"][0]["message"]["content"]
            result = LocalResult(text.strip() or None)
            _last_result = result
            return result
        except Exception as e:
            log.warning("local LLM generation failed: %s", e)
            result = LocalResult(None, f"Local model generation failed: {e}")
            _last_result = result
            return result


# ── External OpenAI-compatible API ────────────────────────────────────────────

# AI-03: transient failures get bounded retries; permanent 4xx never retry.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_API_MAX_ATTEMPTS = 3          # 1 initial attempt + at most TWO retries
_RETRY_AFTER_CAP_S = 8.0       # Retry-After is honored but capped
_backoff_sleep = time.sleep    # module-level so tests can monkeypatch


def _api_chat(settings: dict, system: str, user: str, max_tokens: int) -> LocalResult:
    global _last_result
    key = decrypt_str(settings.get("ai_api_key_enc") or "")
    if not key:
        result = LocalResult(None, "No API key configured — add it in "
                                   "Settings → Notifications → AI assistant.")
        _last_result = result
        return result
    base = str(settings.get("ai_api_base") or DEFAULT_API_BASE).rstrip("/")
    model_name = str(settings.get("ai_api_model") or DEFAULT_API_MODEL)
    # AI-01 single egress choke point: everything serialized into an external
    # request body passes the sanitizer boundary. Deterministic + idempotent;
    # redaction is logged at debug level as counts only. Retries re-send the
    # SAME sanitized payload (idempotent request type).
    system = sanitize_outbound_text(system)
    user = sanitize_outbound_text(user)
    payload = {"model": model_name,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "max_tokens": int(max_tokens), "temperature": 0.7}
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    diag = ""
    for attempt in range(_API_MAX_ATTEMPTS):
        try:
            resp = requests.post(f"{base}/chat/completions",
                                 headers=headers, json=payload, timeout=15)
            status = getattr(resp, "status_code", None)
            if status is not None and status in _TRANSIENT_STATUS:
                if attempt < _API_MAX_ATTEMPTS - 1:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        delay = min(float(retry_after), _RETRY_AFTER_CAP_S)
                    except (TypeError, ValueError):
                        delay = min(2.0 ** attempt, _RETRY_AFTER_CAP_S)
                    log.info("LLM API transient %s (attempt %d/%d), "
                             "retrying in %.1fs", status, attempt + 1,
                             _API_MAX_ATTEMPTS, delay)
                    diag = f"provider returned {status}; retrying"
                    _backoff_sleep(delay)
                    continue
                diag = ("The AI provider is temporarily unavailable "
                        f"({status}). Please try again shortly.")
                break
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            result = LocalResult(text.strip() or None)
            _last_result = result
            return result
        except requests.exceptions.HTTPError as e:
            # Permanent client error (4xx beyond the transient set): never retry.
            status = getattr(e.response, "status_code", None)
            if status == 401 or status == 403:
                diag = "The API key was rejected — check it in Settings → Notifications → AI assistant."
            else:
                diag = ("The API request failed — check the API key and base "
                        "URL in Settings → Notifications → AI assistant.")
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < _API_MAX_ATTEMPTS - 1:
                delay = min(2.0 ** attempt, _RETRY_AFTER_CAP_S)
                log.info("LLM API %s (attempt %d/%d), retrying in %.1fs",
                         type(e).__name__, attempt + 1, _API_MAX_ATTEMPTS, delay)
                diag = "provider unreachable; retrying"
                _backoff_sleep(delay)
                continue
            diag = "The AI provider could not be reached — check your connection."
            break
        except Exception as e:
            # Never echo the key: the exception may carry the request URL only.
            log.warning("LLM API request failed (%s): %s", type(e).__name__, e)
            diag = ("The API request failed — check the API key and base URL "
                    "in Settings → Notifications → AI assistant.")
            break
    result = LocalResult(None, diag)
    _last_result = result
    return result


# ── Public generators ─────────────────────────────────────────────────────────

def _generate(settings: dict, system: str, user: str, max_tokens: int = 256) -> LocalResult:
    """Run one generation and return a LocalResult (text + diagnostic).
    Internal — the public generators unwrap .text and still return str | None;
    callers that need the failure reason use local_diagnostic()."""
    provider = resolve_provider(settings)
    if provider == "local":
        return _local_chat(settings, system, user, max_tokens)
    if provider == "api":
        return _api_chat(settings, system, user, max_tokens)
    return LocalResult(None, "")


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
    return _generate(settings, _SUMMARY_SYSTEM, user).text


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
    return _generate(settings, _NARRATIVE_SYSTEM, user).text


# ── Chat over your own data ───────────────────────────────────────────────────

_ASK_SYSTEM = (
    "You answer questions about the user's OWN personal-finance data. Use "
    "ONLY the numbers in the DATA block — never invent figures, never guess. "
    "You may do simple arithmetic (sums, averages, percentages) on the "
    "provided numbers. No financial advice, no predictions, no markdown. "
    "Answer in 1-4 plain sentences. If the DATA cannot answer the question, "
    "say exactly that and suggest what other data might help."
)


def build_data_context(user_id: int, settings: dict) -> str:
    """A compact, sanitized snapshot of the user's financial data for the
    chat prompt: numeric aggregates only, every free-text value stripped of
    newlines and capped, so stored data can never inject instructions."""
    import pandas as pd
    from datetime import date as _date, timedelta as _td
    from db import get_expenses, get_income, get_savings, get_loans, get_recurring
    from gamification import get_logging_streak

    today = _date.today()
    first_this = today.replace(day=1)
    first_prev = (first_this - _td(days=1)).replace(day=1)

    expenses = get_expenses(user_id)
    income = get_income(user_id)

    def _month(df, start):
        if df.empty:
            return df
        start_ts = pd.Timestamp(start)
        end = start_ts + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)
        return df[(df["date"] >= start_ts) & (df["date"] < end)]

    def _sum(df, col):
        return round(float(df[col].fillna(0).sum()), 2) if not df.empty else 0.0

    lines = [f"Date: {today.isoformat()}"]
    exp_m = _month(expenses, first_this)
    exp_p = _month(expenses, first_prev)
    inc_m = _month(income, first_this)
    inc_p = _month(income, first_prev)

    lines.append(f"Current month ({first_this.strftime('%Y-%m')}):")
    lines.append(f"- expenses: {len(exp_m)} entries, {_sum(exp_m, 'amount_eur')} EUR total")
    if not exp_m.empty:
        cats = exp_m.groupby("category")["amount_eur"].sum().nlargest(5)
        lines.append("- top expense categories: "
                     + "; ".join(f"{_sanitize_stat(c)} ({round(float(a), 2)})"
                                 for c, a in cats.items()))
    lines.append(f"- income: {len(inc_m)} entries, {_sum(inc_m, 'actual_eur')} EUR total")
    budget_total = float(settings.get("monthly_budget") or 0.0)
    if budget_total > 0:
        spent = _sum(exp_m, "amount_eur")
        lines.append(f"- monthly budget: {round(budget_total, 2)} EUR "
                     f"(remaining {round(max(budget_total - spent, 0), 2)} EUR)")
    fun_allowance = float(settings.get("fun_money") or 0.0)
    if fun_allowance > 0:
        lines.append(f"- fun-money allowance: {round(fun_allowance, 2)} EUR")
    savings = get_savings(user_id)
    if not savings.empty:
        for name in savings["goal_name"].fillna("").unique():
            rows = savings[savings["goal_name"].fillna("") == name]
            if rows.empty:
                continue
            last = rows.sort_values("date").iloc[-1]
            bal = float(last["balance_eur"]) if last["balance_eur"] == last["balance_eur"] else 0.0
            tgt = float(last["target_eur"]) if last["target_eur"] == last["target_eur"] else 0.0
            lines.append(f"- savings goal '{_sanitize_stat(name)}': balance "
                         f"{round(bal, 2)} of {round(tgt, 2)} EUR")
    loans = get_loans(user_id)
    if not loans.empty:
        for _, r in loans.iterrows():
            if str(r.get("status")) != "active":
                continue
            _prin = float(r.get("principal_eur") or 0)
            _rate = float(r.get("annual_rate") or 0)
            if _prin != _prin:
                _prin = 0.0
            if _rate != _rate:
                _rate = 0.0
            lines.append(f"- loan '{_sanitize_stat(r.get('name'))}': "
                         f"{round(_prin, 2)} EUR principal, "
                         f"{round(_rate, 2)}% annual rate")
    recurring = get_recurring(user_id)
    if not recurring.empty:
        active = recurring[recurring["active"].fillna(False).astype(bool)]
        bills = []
        for _, r in active.head(8).iterrows():
            _amt = float(r["amount_eur"] or 0)
            if _amt != _amt:
                _amt = 0.0
            bills.append(f"{_sanitize_stat(r['description'])} {round(_amt, 2)} EUR")
        lines.append(f"- recurring bills: {len(active)} ({'; '.join(bills)})")

    lines.append(f"Previous month ({first_prev.strftime('%Y-%m')}): "
                 f"expenses {_sum(exp_p, 'amount_eur')} EUR, "
                 f"income {_sum(inc_p, 'actual_eur')} EUR")
    lines.append(f"All time: {len(expenses)} expenses, {len(income)} income entries")
    lines.append(f"Current logging streak: {get_logging_streak(expenses)} days")

    recent = expenses.sort_values("date", ascending=False).head(10)
    if not recent.empty:
        parts = []
        for _, r in recent.iterrows():
            _amt = float(r["amount_eur"] or 0)
            if _amt != _amt:
                _amt = 0.0
            _when = r["date"].date().isoformat() if pd.notna(r["date"]) else "?"
            parts.append(f"{_sanitize_stat(r['description'])} "
                         f"({_sanitize_stat(r['category'])}, {round(_amt, 2)} EUR, {_when})")
        lines.append("Recent expenses: " + "; ".join(parts))
    return "\n".join(lines)


def answer_query(user_id: int, question: str, settings: dict,
                 history: list | None = None) -> str | None:
    """Answer a natural-language question about the user's own data.

    `history`: optional list of {"role": "user"|"assistant", "content": str}
    turns so follow-up questions keep context; every turn is sanitized and
    capped. The question and every data field are sanitized before they
    reach the model; ANY failure — including a crash while building the
    data context — returns None so the caller can show a fallback."""
    if resolve_provider(settings) == "none":
        return None
    q = _sanitize_stat(question or "")
    if not q.strip():
        return None
    chat_so_far = ""
    if history:
        turns = []
        for h in history[-4:]:
            role = str(h.get("role", "user")) if isinstance(h, dict) else "user"
            content = h.get("content", "") if isinstance(h, dict) else str(h)
            content = _sanitize_stat(content)[:200]
            turns.append(f"{role}: {content}")
        if turns:
            chat_so_far = "CHAT SO FAR:\n" + "\n".join(turns) + "\n\n"
    try:
        context = build_data_context(user_id, settings)
        user = (f"{chat_so_far}DATA:\n{context}\n\nQUESTION:\n{q}\n\n"
                "Answer the question now.")
        return _generate(settings, _ASK_SYSTEM, user, max_tokens=300).text
    except Exception as e:
        log.warning("answer_query failed: %s", e)
        return None
