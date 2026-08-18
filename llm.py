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
from pathlib import Path

import requests

from crypto import decrypt_str

log = logging.getLogger("llm")

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_API_MODEL = "google/gemma-3-12b-it"
DEFAULT_LOCAL_MODEL_FILENAME = "google_gemma-3-1b-it-Q4_K_M.gguf"

# One local generation at a time; the model is loaded once per path.
_local_lock = threading.Lock()
_local_cache: tuple = (None, None)  # (model_path, llama instance)


# ── Provider resolution ───────────────────────────────────────────────────────

def find_bundled_model() -> str | None:
    """Return the app-local Gemma path when the optional GGUF is present."""
    path = (Path(__file__).resolve().parent / "models"
            / DEFAULT_LOCAL_MODEL_FILENAME)
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

def _get_local_model(settings: dict):
    """Load (once) and return the llama-cpp model for the configured path."""
    global _local_cache
    path = (str(settings.get("ai_local_model") or "").strip()
            or find_bundled_model())
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
        return _generate(settings, _ASK_SYSTEM, user, max_tokens=300)
    except Exception as e:
        log.warning("answer_query failed: %s", e)
        return None
