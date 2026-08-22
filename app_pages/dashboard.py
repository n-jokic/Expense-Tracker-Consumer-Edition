"""
Dashboard page: KPIs, budget alerts, spending charts, monthly trends.
"""

import calendar
import math
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import queries as q
from db import add_expense
from services.finance_queries import (
    get_savings_summary, unallocated_breakdown, allocation_donut_slices,
)
from notifications import _unlogged_templates
from utils import (
    NEAR_LIMIT_THRESHOLD, SAVINGS_TARGET_PCT, SAVINGS_GOAL_PCT, CHART_COLORS,
    fmt, fmt_row, to_display, get_currency_symbol, effective_category_budgets,
    filter_started_templates, progress_ratio, to_eur,
    CAT_LIST, CATEGORIES, SUPPORTED_CURRENCIES,
)
from ui.panel import PanelSpec, panel
from ui.styles import C_BLUE, C_NEG, C_POS, C_PRIMARY, C_PRIMARY_SOFT, C_WARN

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
SYM     = get_currency_symbol(DC)

dfe = q.expenses(user_id)
dfi = q.income(user_id)
dfs = q.savings(user_id)
dfb = q.budgets(user_id)

# Household toggle (#21a): derive membership from the DB each run — the
# session_state copy goes stale when leave/join happens on another device.
hh_id = q.current_household_id(user_id)
personal_view = True
if hh_id:
    view = st.segmented_control("View", ["My data", "Household"], default="My data", key="dash_view")
    if view == "Household":
        personal_view = False
        dfe = q.household_expenses(hh_id)

# ── Personal task hub (Personal mode only) ──────────────────────────────────
if personal_view:
    spec = PanelSpec(id="dash_quick_actions", title="Quick actions",
                     icon=":material/bolt:", collapsible=True, default_expanded=True)
    expanded, container = panel(spec, user_id=user_id, area="dashboard")
    if expanded:
        with container:
            qa1, qa2, qa3 = st.columns(3)
            with qa1:
                st.page_link("app_pages/log_expense.py", label="Log expense",
                             icon=":material/receipt_long:", width="stretch")
            with qa2:
                st.page_link("app_pages/log_income.py", label="Log income",
                             icon=":material/payments:", width="stretch")
            with qa3:
                st.page_link("app_pages/settings.py", label="Open settings",
                             icon=":material/tune:", width="stretch")
            st.caption("Budgets live in Settings — add or edit them there.")

    # Upcoming bills: active recurring templates with a due day within the
    # next 7 calendar days.
    rec_df  = q.recurring(user_id)
    today   = date.today()

    def _next_due(day, base):
        """Next occurrence of a due day-of-month on/after base, clamped to the
        month length (e.g. due_day 31 in a 30-day month)."""
        def _clamp(y, m):
            return min(day, calendar.monthrange(y, m)[1])
        if base.day <= day:
            d = date(base.year, base.month, _clamp(base.year, base.month))
            if d >= base:
                return d
        y, m = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
        return date(y, m, _clamp(y, m))

    upcoming = []
    if not rec_df.empty:
        started = filter_started_templates(
            rec_df[rec_df["active"] == True], today.year, today.month)
        # Skip bills already logged this month (same "done" logic as the
        # Recurring checklist — a paid bill must not appear as upcoming).
        logged_tids = set()
        if not dfe.empty and "rec_template_id" in dfe.columns:
            tm = dfe[(dfe["date"].dt.year == today.year) &
                     (dfe["date"].dt.month == today.month)]
            logged_tids = set(tm["rec_template_id"].dropna().astype(str))
        for _, r in started.iterrows():
            if str(r["id"]) in logged_tids:
                continue
            dd = r["due_day"]
            if dd is None or pd.isna(dd):
                continue
            d = _next_due(int(dd), today)
            if 0 <= (d - today).days <= 7:
                upcoming.append((d, r))
    if upcoming:
        spec = PanelSpec(id="dash_upcoming_bills", title="Upcoming bills",
                         icon=":material/event:", collapsible=True, default_expanded=True)
        expanded, container = panel(spec, user_id=user_id, area="dashboard")
        if expanded:
            with container:
                for d, r in sorted(upcoming, key=lambda t: t[0]):
                    desc = (r["description"] if pd.notna(r["description"])
                            else (r["category"] if pd.notna(r["category"]) else "Bill"))
                    amt  = fmt_row(r["amount_eur"], r["amount"], r["currency"], DC, rates)
                    st.markdown(f"- {d.strftime('%d %b')} — **{desc}** · {amt}")

    # ── Where your money goes (D1): transparent allocation overview ──────────
    # Read-only consumer of the FIN-01 services — no invariant changes.
    _alloc = unallocated_breakdown(user_id)
    spec = PanelSpec(id="dash_allocation", title="Where your money goes",
                     icon=":material/pie_chart:", collapsible=True,
                     default_expanded=True,
                     summary="live balance of every euro")
    expanded, container = panel(spec, user_id=user_id, area="dashboard")
    if expanded:
        with container:
            m1, m2, m3 = st.columns(3)
            m1.metric("Unallocated now", fmt(_alloc["unallocated_eur"], DC, rates))
            m2.metric("Received (incl. financing)",
                      fmt(_alloc["inflows_eur"] + _alloc["financing_inflows_eur"], DC, rates))
            m3.metric("Spent so far", fmt(_alloc["outflows_eur"], DC, rates))

            with st.expander("Where it is allocated", icon=":material/account_tree:"):
                # #24: allocation as a donut — same data as the old bullet
                # list, now visual; zero slices are dropped so empty goals
                # do not render phantom wedges.
                _slices = allocation_donut_slices(user_id)
                if _slices:
                    _fig = go.Figure(go.Pie(
                        labels=[lbl for lbl, _ in _slices],
                        values=[v for _, v in _slices],
                        hole=0.45, sort=False,
                        marker=dict(colors=CHART_COLORS),
                        hovertemplate="%{label}: %{value:,.2f} EUR (%{percent})<extra></extra>",
                    ))
                    _fig.update_layout(margin=dict(l=8, r=8, t=8, b=8),
                                       showlegend=True,
                                       legend=dict(orientation="h", y=-0.12),
                                       paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(_fig, key="dash_alloc_donut")
                else:
                    st.caption("Nothing allocated yet — fund a goal, open a "
                               "term deposit or buy a holding to see it here.")
                for lbl, v in _slices:
                    st.markdown(f"- {lbl} · {fmt(v, DC, rates)}")
                _parts = (_alloc["savings_allocations_eur"] + _alloc["term_allocations_eur"]
                          + _alloc["holdings_allocations_eur"])
                st.caption(
                    f"Check: received − spent − allocated ({fmt(_parts, DC, rates)}) "
                    f"= unallocated. This is the app's core balance rule; "
                    "every panel reconciles against it.")

            # Planning layers — labelled OUTSIDE the balance rule: budgets and
            # unpaid bills guide spending but are not part of FIN-01 money.
            with st.expander("This month's plan (planning only)",
                             icon=":material/event_note:"):
                _bud_total = float(sum(effective_category_budgets(dfb).values()))
                _month_exp = dfe[(dfe["date"].dt.year == today.year)
                                 & (dfe["date"].dt.month == today.month)] if not dfe.empty else dfe
                _month_spent = float(_month_exp["amount_eur"].fillna(0).sum()) \
                    if not _month_exp.empty else 0.0
                p1, p2 = st.columns(2)
                p1.metric("Category budgets", fmt(_bud_total, DC, rates),
                          delta=f"{fmt(_month_spent, DC, rates)} spent",
                          delta_color="off")
                _reserve_rows = []
                if not rec_df.empty:
                    try:
                        _reserve_rows = _unlogged_templates(rec_df, dfe, today)
                    except Exception:
                        _reserve_rows = []
                _reserve = float(sum(float(r["amount_eur"]) if pd.notna(r["amount_eur"]) else 0.0
                                     for r in _reserve_rows))
                p2.metric("Upcoming-bills reserve", fmt(_reserve, DC, rates),
                          delta=f"{len(_reserve_rows)} bill(s) unpaid this month",
                          delta_color="off")
                st.caption("Budgets and this reserve are planning aids — "
                           "they do not change your unallocated balance.")

    # ── Auto-allocation rules editor (D2 / item 11) ──────────────────────────
    _aar = st.session_state.settings.get("auto_alloc_rules") or {}
    if not isinstance(_aar, dict):
        _aar = {}
    _aar = {"enabled": bool(_aar.get("enabled")),
            "targets": list(_aar.get("targets") or [])}
    _goal_opts = [g["goal_name"] for g in get_savings_summary(user_id)["goals"]]
    _loans_df = q.loans(user_id)
    _loan_map = {} if _loans_df.empty else {
        str(r["id"]): str(r["name"] or r["id"]) for _, r in _loans_df.iterrows()}
    @st.fragment
    def _aar_rules_editor(user_id, _aar, _goal_opts, _loan_map, DC, rates):
        # research.md U2: every keystroke here reruns ONLY this fragment -
        # not the seven plotly figures below. Saves still rerun the full app
        # (data changed), which is exactly the wanted behaviour.
        _total_pct = 0.0
        for i, t in enumerate(_aar["targets"]):
            k = f"aar_{i}"
            # research.md U6: stacked 2x2 grid for phone width; keys unchanged.
            r1c1, r1c2 = st.columns([1.1, 2.2])
            r2c1, r2c2 = st.columns([0.9, 0.4])
            ttype = r1c1.selectbox(
                "Type", ["goal", "loan"],
                index=0 if t.get("type") == "goal" else 1,
                key=f"{k}_type", label_visibility="collapsed")
            if ttype == "goal":
                opts = _goal_opts or [str(t.get("ref")) or "(no goals yet)"]
                cur = t.get("ref") if t.get("ref") in opts else opts[0]
                ref = r1c2.selectbox("Target", opts,
                                     index=opts.index(cur),
                                     key=f"{k}_ref",
                                     label_visibility="collapsed")
            else:
                pairs = [(lid, lname) for lid, lname in _loan_map.items()]
                if not pairs:
                    pairs = [(t.get("ref") or "", "(no active loans)")]
                ids = [pid for pid, _ in pairs]
                labels2 = [pname for _, pname in pairs]
                cur_i = ids.index(t.get("ref")) if t.get("ref") in ids else 0
                ref = ids[cur_i]
                r1c2.selectbox("Loan", labels2, index=cur_i,
                               key=f"{k}_ref", label_visibility="collapsed")
            pct = r2c1.number_input("%", min_value=0.0, max_value=100.0,
                                    step=1.0,
                                    value=float(t.get("pct") or 0.0),
                                    key=f"{k}_pct",
                                    label_visibility="collapsed")
            _total_pct += float(pct)
            drop = r2c2.checkbox("✖", value=False, key=f"{k}_del",
                                 help="Remove this rule")
            t["type"], t["ref"], t["pct"] = ttype, ref, float(pct)
            t["_drop"] = drop
        kept = [dict(t) for t in _aar["targets"] if not t.get("_drop")]
        for t in kept:
            t.pop("_drop", None)
        b_add2, _fill = st.columns([1, 3])
        if b_add2.button("+ Add target", width="stretch"):
            kept.append({"type": "goal",
                         "ref": (_goal_opts[0] if _goal_opts else ""),
                         "pct": 10.0})
            q.save_settings(user_id, {"auto_alloc_rules":
                                      {"enabled": True, "targets": kept}})
            q.bump_db_version()
            st.rerun()
        _norm = [{kk: t[kk] for kk in ("type", "ref", "pct")} for t in kept]
        if _norm != _aar["targets"]:
            if st.button("Save rules", type="primary", width="stretch",
                         icon=":material/save:"):
                q.save_settings(user_id, {"auto_alloc_rules":
                                          {"enabled": True,
                                           "targets": _norm}})
                q.bump_db_version()
                st.toast("Rules saved.", icon=":material/check:")
                st.rerun()
        if _total_pct > 100.0:
            st.warning(f"Rules add up to {_total_pct:g}% of the income — "
                       "they will be scaled down pro-rata whenever the "
                       "unallocated pool is tight.")
        _remainder = max(0.0, 100.0 - _total_pct)
        st.caption(f"{_total_pct:g}% auto-allocated · {_remainder:g}% "
                   "stays in unallocated funds (display only).")

    spec = PanelSpec(id="dash_auto_alloc", title="Auto-allocation of income",
                     icon=":material/call_split:", collapsible=True,
                     default_expanded=False,
                     summary=("ON · " + str(len(_aar["targets"])) + " rule(s)"
                              if _aar["enabled"] else "off"))
    expanded, container = panel(spec, user_id=user_id, area="dashboard")
    if expanded:
        with container:
            _new_enabled = st.toggle(
                "Split every logged income automatically",
                value=_aar["enabled"], key="aar_enabled_toggle",
                help="Goals receive a deposit; LOAN targets move real money "
                     "as early repayments at log time.")
            if _new_enabled != _aar["enabled"]:
                _aar["enabled"] = _new_enabled
                q.save_settings(user_id, {"auto_alloc_rules": _aar})
                q.bump_db_version()
                st.rerun()
            if not _aar["enabled"]:
                st.caption("Turn on to split each new income entry across your "
                           "goals and loans.")
            else:
                # research.md U2: editor runs as a fragment (see def above).
                _aar_rules_editor(user_id, _aar, _goal_opts, _loan_map, DC, rates)
            _last = st.session_state.get("last_auto_alloc")
            if isinstance(_last, dict) and _last.get("enabled"):
                with st.expander("Last run", icon=":material/history:"):
                    for a in _last.get("applied", []):
                        st.markdown(f"- ✅ {a['ref']} · {fmt(a['amount_eur'], DC, rates)}")
                    for s2 in _last.get("skipped", []):
                        st.markdown(f"- ⏭️ {s2['ref']} · skipped")
                    for e2 in _last.get("errors", []):
                        st.markdown(f"- ⚠️ {e2['ref']} · {e2.get('error', 'failed')}")
                    if _last.get("scaled"):
                        st.caption("Requests exceeded the pool — all targets were "
                                   "scaled down pro-rata.")
    # One-tap quick logging (C1) — BISECTION VARIANT B1: no @st.dialog.
    @st.fragment
    def _preset_editor(user_id, presets, edit_key, DC, rates):
        # research.md U2: typing here reruns only this fragment.
        # research.md U2: typing here reruns only this fragment.
        gen = int(st.session_state.get("dash_qp_gen", 0))
        draft = st.session_state.get("dash_qp_draft")
        if draft is None:
            draft = [dict(p) for p in presets]
            st.session_state["dash_qp_draft"] = draft
        st.caption("Edit your one-tap buttons. Amounts are in each "
                   "row's own currency; Del removes the row.")
        kept = []
        for i, p in list(enumerate(draft)):
            k = f"qp{gen}_{i}"
            # research.md U6: two stacked rows of three - six side-by-side
            # inputs are unusable at phone width. Widget keys unchanged.
            r1c1, r1c2, r1c3 = st.columns([1.3, 1, 1])
            r2c1, r2c2, r2c3 = st.columns([1.5, 1.5, 0.45])
            label = r1c1.text_input("Label", value=p["label"],
                                    key=f"{k}_label", label_visibility="collapsed")
            amt = r1c2.number_input("Amount", min_value=0.0, step=0.10,
                                    format="%.2f", value=float(p["amount"]),
                                    key=f"{k}_amt", label_visibility="collapsed")
            curs = list(SUPPORTED_CURRENCIES.keys())
            cur = r1c3.selectbox("Currency", curs,
                                 index=curs.index(p["currency"])
                                 if p["currency"] in curs else 0,
                                 key=f"{k}_cur", label_visibility="collapsed")
            cat = r2c1.selectbox("Category", CAT_LIST,
                                 index=CAT_LIST.index(p["category"])
                                 if p["category"] in CAT_LIST else 0,
                                 key=f"{k}_cat", label_visibility="collapsed")
            subs = ["—"] + CATEGORIES.get(cat, [])
            sub0 = p["subcategory"] if p["subcategory"] in subs[1:] else "—"
            sub = r2c2.selectbox("Subcategory", subs,
                                 index=subs.index(sub0),
                                 key=f"{k}_sub", label_visibility="collapsed")
            drop = r2c3.checkbox("Del", value=False, key=f"{k}_del")
            if not drop:
                kept.append({
                    "id": p["id"],
                    "label": label.strip() or p["label"],
                    "amount": float(amt), "currency": cur,
                    "category": cat,
                    "subcategory": "" if sub == "—" else sub,
                    "description": p["description"],
                })
        # Keep the working copy in sync so "+ Add preset" doesn't
        # discard edits typed this run (same widget keys survive).
        st.session_state["dash_qp_draft"] = kept
        b_add, b_save, b_cancel, b_done, _sp = st.columns([1, 1, 1, 1, 1])
        if b_add.button("+ Add preset", width="stretch"):
            kept.append({"id": f"p{len(kept)}-{gen}", "label": "",
                         "amount": 0.0, "currency": DC,
                         "category": CAT_LIST[0], "subcategory": "",
                         "description": f"Quick {len(kept) + 1}"})
            st.session_state["dash_qp_draft"] = kept
            st.rerun()
        if b_cancel.button("Cancel", width="stretch"):
            st.session_state.pop("dash_qp_draft", None)
            st.session_state[edit_key] = False
            st.rerun()
        if b_done.button("✓ Done", width="stretch"):
            st.session_state.pop("dash_qp_draft", None)
            st.session_state[edit_key] = False
            st.rerun()
        if b_save.button("Save presets", type="primary", width="stretch"):
            q.save_settings(user_id, {"quick_presets": kept})
            st.session_state.pop("dash_qp_draft", None)
            st.session_state["dash_qp_gen"] = gen + 1
            st.session_state[edit_key] = False
            q.bump_db_version()
            st.toast("Presets saved.", icon=":material/check:")
            st.rerun()


    _QP_DEFAULTS = [
        {"id": "coffee", "label": "☕ Coffee", "amount": 2.50, "currency": "EUR",
         "category": "Dining Out", "subcategory": "Coffee & Snacks",
         "description": "Coffee"},
        {"id": "lunch", "label": "🍔 Lunch", "amount": 10.00, "currency": "EUR",
         "category": "Dining Out", "subcategory": "Work Lunch",
         "description": "Lunch"},
        {"id": "transit", "label": "🚌 Transit", "amount": 2.00, "currency": "EUR",
         "category": "Transport", "subcategory": "Public Transit",
         "description": "Transit"},
    ]
    def _load_presets():
        raw = st.session_state.settings.get("quick_presets") or []
        out = []
        if isinstance(raw, list):
            for i, p in enumerate(raw):
                try:
                    cat = p.get("category") if p.get("category") in CAT_LIST else CAT_LIST[0]
                    sub = str(p.get("subcategory") or "")
                    if sub and sub not in CATEGORIES.get(cat, []):
                        sub = ""
                    out.append({
                        "id": str(p.get("id") or f"p{i}"),
                        "label": str(p.get("label") or f"Preset {i + 1}"),
                        "amount": float(p.get("amount") or 0.0),
                        "currency": (p.get("currency")
                                     if p.get("currency") in SUPPORTED_CURRENCIES else "EUR"),
                        "category": cat, "subcategory": sub,
                        "description": str(p.get("description") or f"Quick {i + 1}"),
                    })
                except Exception:
                    continue
        return out or [dict(x) for x in _QP_DEFAULTS]
    presets = _load_presets()
    edit_key = "dash_one_tap_edit"
    spec = PanelSpec(id="dash_one_tap", title="One-tap logging",
                     icon=":material/touch_app:", collapsible=True, default_expanded=True)
    def _open_qp_edit():
        # Stable label/key: the flag must never change widget identity.
        st.session_state[edit_key] = True
    expanded, container = panel(
        spec, user_id=user_id, area="dashboard",
        actions=[("✏️ Edit presets", _open_qp_edit)])
    # Read AFTER panel(): a click during THIS run must take effect
    # immediately — that is also what AppTest needs to see the editor.
    editing = bool(st.session_state.get(edit_key))
    def _quick_log(preset, amount=None):
        amt = float(preset["amount"] if amount is None else amount)
        cur = preset["currency"]
        desc = preset["description"]
        eur = to_eur(amt, cur, rates)
        qa_key = f"qa_{preset['id']}_{today.isoformat()}"
        if st.session_state.get(qa_key):
            st.toast("Already saved — duplicate prevented.", icon=":material/check:")
            return
        fresh = q.expenses(user_id)
        if not fresh.empty and (
                (fresh["date"].dt.date == today)
                & (fresh["description"] == desc)
                & (fresh["amount_eur"].round(2) == round(eur, 2))).any():
            st.session_state[qa_key] = True
            st.toast("Already saved — duplicate prevented.", icon=":material/check:")
            return
        try:
            add_expense(user_id, {
                "date": today, "category": preset["category"],
                "subcategory": preset["subcategory"], "description": desc,
                "amount": amt, "currency": cur, "amount_eur": eur,
                "recurring": False, "notes": "Quick-add",
            })
        except Exception as e:
            st.error(f"Couldn't save: {e}")
        else:
            q.bump_db_version()
            st.toast(f"{preset['label']} logged — {fmt(eur, DC, rates)}",
                     icon=":material/check:")
    if expanded:
        with container:
            if editing:
                # research.md U2: editor runs as a fragment (def above).
                _preset_editor(user_id, presets, edit_key, DC, rates)
            else:
                adj_id = st.session_state.get("qa_adjust_id")
                for row_start in range(0, len(presets), 3):
                    chunk = presets[row_start:row_start + 3]
                    cols = st.columns(3)
                    for col, p in zip(cols, chunk):
                        with col:
                            eur0 = to_eur(float(p["amount"]), p["currency"], rates)
                            if st.button(f"{p['label']} · {fmt(eur0, DC, rates)}",
                                         key=f"qa_go_{p['id']}", width="stretch"):
                                _quick_log(p)
                                st.rerun()
                            if st.button("✎", key=f"qa_adj_open_{p['id']}",
                                         help="Adjust the price just for this log"):
                                st.session_state["qa_adjust_id"] = (
                                    None if adj_id == p["id"] else p["id"])
                                st.rerun()
                # Inline adjust panel (no st.dialog: dialogs stall AppTest).
                adj = next((p for p in presets if p["id"] == adj_id), None)
                if adj is not None:
                    sym = get_currency_symbol(adj["currency"])
                    with st.container(border=True):
                        st.markdown(f"**Adjust “{adj['label']}” for this log**")
                        amt = st.number_input(f"Amount ({sym})", min_value=0.0,
                                              step=0.10, format="%.2f",
                                              value=float(adj["amount"]),
                                              key=f"qa_adj_val_{adj['id']}")
                        c_log, c_cancel, _rest = st.columns([1, 1, 2])
                        if c_log.button("Log it", type="primary", width="stretch"):
                            _quick_log(adj, amount=float(amt))
                            st.session_state.pop("qa_adjust_id", None)
                            st.rerun()
                        if c_cancel.button("Cancel", width="stretch"):
                            st.session_state.pop("qa_adjust_id", None)
                            st.rerun()
    # Recent activity: the 5 most recent expenses.
    spec = PanelSpec(id="dash_recent", title="Recent activity",
                     icon=":material/history:", collapsible=True, default_expanded=True)
    expanded, container = panel(spec, user_id=user_id, area="dashboard")
    if expanded:
        with container:
            recent = dfe.head(5)
            if recent.empty:
                st.caption("No expenses logged yet.")
                st.page_link("app_pages/log_expense.py", label="Log your first expense",
                             icon=":material/receipt_long:")
            else:
                rec = recent[["date", "description", "category", "amount", "currency", "amount_eur"]].copy()
                rec["date"] = rec["date"].dt.strftime("%d %b %Y").fillna("")
                rec["Amount"] = rec.apply(lambda r: fmt_row(r["amount_eur"], r["amount"],
                                                            r["currency"], DC, rates), axis=1)
                st.dataframe(
                    rec[["date", "description", "category", "Amount"]].rename(
                        columns={"date": "Date", "description": "Description",
                                 "category": "Category"}),
                    hide_index=True, width="stretch",
                    column_config={
                        "Date": st.column_config.TextColumn("Date"),
                        "Description": st.column_config.TextColumn("Description"),
                        "Category": st.column_config.TextColumn("Category"),
                        "Amount": st.column_config.TextColumn("Amount"),
                    },
                )

if personal_view and dfe.empty and dfi.empty:
    st.info("No data yet — start logging expenses or income.")
    st.stop()

ayrs = sorted(set(
    (list(dfe["date"].dropna().dt.year.unique()) if not dfe.empty else []) +
    (list(dfi["date"].dropna().dt.year.unique()) if not dfi.empty else [])
), reverse=True) or [date.today().year]

# research.md U3: period filters live in the sidebar (dashboards guidance),
# beside the currency control, freeing a full page row for content.
with st.sidebar:
    sy = st.selectbox("Year", ayrs, key="dash_year")
    mo_opts = ["All months"] + [calendar.month_name[m] for m in range(1, 13)]
    sml = st.select_slider("Month", mo_opts, key="dash_month")
    sm  = mo_opts.index(sml)

def flt(df):
    if df.empty: return df
    mask = df["date"].dt.year == sy
    if sm > 0: mask = mask & (df["date"].dt.month == sm)
    return df[mask]

def prev_flt(df):
    """Same filter, shifted one period back (month or year)."""
    if df.empty: return df
    if sm > 0:
        py, pm = (sy, sm - 1) if sm > 1 else (sy - 1, 12)
        mask = (df["date"].dt.year == py) & (df["date"].dt.month == pm)
    else:
        mask = df["date"].dt.year == sy - 1
    return df[mask]

def _delta(cur, prev):
    if prev and prev > 0:
        pct = (cur - prev) / prev * 100
        return f"{abs(pct):.0f}% vs prev"
    return ""

exp   = flt(dfe)
inc   = flt(dfi)
svyr  = dfs[dfs["date"].dt.year == sy] if not dfs.empty else dfs
prev_exp = prev_flt(dfe)
prev_inc = prev_flt(dfi)

ie = float(inc["actual_eur"].sum())  if not inc.empty  else 0.0
ee = float(exp["amount_eur"].sum())  if not exp.empty  else 0.0
sd = float(svyr["deposited_eur"].sum()) if not svyr.empty else 0.0
ne = ie - ee - sd
sr = (sd / ie * 100) if ie > 0 else 0.0

pie = float(prev_inc["actual_eur"].sum()) if not prev_inc.empty else 0.0
pee = float(prev_exp["amount_eur"].sum()) if not prev_exp.empty else 0.0

# ── research.md U4: hoisted KPI inputs so the whole band renders as ONE
# horizontal strip (no orphan cards below it). Weekly series feed the metric
# sparklines that replace the standalone sparkline panel.
_week_days = [date.today() - timedelta(days=i) for i in range(6, -1, -1)]
_week_start = pd.Timestamp(_week_days[0])

def _week_series(df, col):
    """Daily EUR totals for the last 7 days, reindexed to the week grid."""
    if df.empty:
        return None
    daily = (df[df["date"] >= _week_start]
             .groupby(df["date"].dt.date)[col].sum())
    if daily.empty:
        return None
    daily = daily.reindex(_week_days, fill_value=0.0)
    return pd.Series([to_display(float(v), DC, rates) for v in daily],
                     index=[d.strftime("%a") for d in _week_days])

_spend_week = _week_series(dfe, "amount_eur")
_income_week = _week_series(dfi, "actual_eur")

rec_df = q.recurring(user_id)
rec_active = (rec_df[rec_df["active"] == True]
              if not rec_df.empty else rec_df)
yearly_fixed = 0.0
if personal_view and not rec_active.empty:
    for _, r in rec_active.iterrows():
        # NB: sm below is the month-filter variable; this is the template start month.
        start_m = str(r.get("start_month") or "").strip()
        months = 12
        if start_m:
            try:
                y, m = int(start_m.split("-")[0]), int(start_m.split("-")[1])
                if y == date.today().year:
                    months = max(0, 13 - m)
                elif y > date.today().year:
                    months = 0
            except (ValueError, TypeError):
                pass
        yearly_fixed += float(r["amount_eur"]) * months

from finance import loan_schedule
df_loans = q.loans(user_id)
total_debt = 0.0
free_dates = []
if personal_view and not df_loans.empty:
    for _, row in df_loans.iterrows():
        if row["status"] != "active":
            continue
        pay_df = q.loan_payments(user_id, str(row["id"]))
        payments = [{
            "date": r["date"].date(),
            "amount_eur": float(r.get("amount_eur") or 0.0),
            "surcharge_eur": float(r.get("loan_surcharge_eur") or 0.0),
        } for _, r in pay_df.iterrows() if pd.notna(r["date"])]
        start_date = (row["start_date"].date() if pd.notna(row["start_date"])
                      else date.today())
        sched = loan_schedule(float(row["principal_eur"]), float(row["annual_rate"]),
                              int(row["term_months"]), start_date,
                              int(row["payment_day"]), payments)
        total_debt += sched["remaining_balance"]
        if sched["payoff_date"]:
            free_dates.append(sched["payoff_date"])

sav_total = 0.0
port_value = 0.0
net_worth = 0.0
if personal_view:
    if not dfs.empty:
        last_bal = (dfs.sort_values("date")
                    .groupby("goal_name")["balance_eur"].last().dropna())
        sav_total = float(last_bal.sum()) if not last_bal.empty else 0.0
    dfh = q.holdings(user_id)
    if not dfh.empty:
        for _, h in dfh.iterrows():
            price = float(h["last_price"]) if pd.notna(h["last_price"]) else 0.0
            qty = float(h["quantity"]) if pd.notna(h["quantity"]) else 0.0
            rt = float(rates.get(str(h["currency"]), 1.0) or 1.0)
            port_value += price * qty / rt
    net_worth = sav_total + port_value - total_debt

if personal_view:
    with st.container(horizontal=True):
        st.metric("Income", fmt(ie, DC, rates), delta=_delta(ie, pie) or None,
                  chart_data=_income_week, border=True)
        st.metric("Expenses", fmt(ee, DC, rates), delta=_delta(ee, pee) or None,
                  delta_color="inverse", chart_data=_spend_week, border=True)
        st.metric("Saved", fmt(sd, DC, rates), border=True)
        st.metric("Net balance", fmt(ne, DC, rates), border=True)
        st.metric("Savings rate", f"{sr:.1f}%", border=True)
        if yearly_fixed > 0:
            st.metric("Fixed costs/yr",
                      f"{fmt(yearly_fixed, DC, rates)} · {len(rec_active)} bills",
                      border=True)
        if total_debt > 0 or free_dates:
            st.metric("Total debt", fmt(total_debt, DC, rates), border=True)
            st.metric("Debt-free by",
                      max(free_dates).strftime("%b %Y") if free_dates else "—",
                      border=True)
        if sav_total or port_value:
            st.metric("Net worth", fmt(net_worth, DC, rates), border=True)

    # research.md U7: one-tap handoff from the dashboard into the advisor.
    _ask_pick = st.pills("Ask AI", [
        "How much did I spend this month?",
        "Where does my money go?",
        "Can I afford a big purchase?",
    ], selection_mode="single")
    if _ask_pick:
        st.query_params["ask"] = _ask_pick[0]
        st.switch_page("app_pages/ask.py")
else:
    # Household spending summary — no personal net balance or savings KPIs.
    hh_members = q.household_members(hh_id)
    hh_top     = (exp.groupby("category")["amount_eur"].sum().idxmax()
                  if not exp.empty else None)
    with st.container(horizontal=True):
        st.metric("Household spending", fmt(ee, DC, rates), border=True)
        st.metric("Members", str(len(hh_members)), border=True)
        st.metric("Top category", hh_top or "—", border=True)
    st.caption("Personal income, savings, budgets, loans and fun money are hidden "
               "in household view — switch to Personal mode to see them.")

# Budget alerts (personal)
if personal_view and not dfb.empty and not exp.empty:
    bf = dfb[dfb["year"] == sy]
    if sm > 0: bf = bf[bf["month"] == sm]
    cb  = effective_category_budgets(bf)
    ca  = exp.groupby("category")["amount_eur"].sum()
    alts = []
    for c in ca.index:
        bud_val = float(cb.get(c, 0))
        act_val = float(ca.get(c, 0))
        if bud_val > 0 and act_val >= bud_val * NEAR_LIMIT_THRESHOLD:
            if act_val > bud_val:
                alts.append(("error", c, act_val, bud_val,
                              f"Over by {fmt(act_val - bud_val, DC, rates)}"))
            else:
                alts.append(("warning", c, act_val, bud_val,
                              f"{act_val / bud_val * 100:.0f}% used"))
    if alts:
        st.subheader("Budget alerts")
        for lvl, c, a, b, msg in alts:
            fn = st.error if lvl == "error" else st.warning
            fn(f"**{c}** — spent {fmt(a, DC, rates)} of {fmt(b, DC, rates)} budget. {msg}",
               icon=":material/error:" if lvl == "error" else ":material/warning:")

# Budget progress bars for the selected month (personal)
if personal_view and sm > 0 and not dfb.empty and not exp.empty:
    bf3 = dfb[(dfb["year"] == sy) & (dfb["month"] == sm)]
    if not bf3.empty:
        st.subheader(f"Budget progress — {calendar.month_name[sm]}")
        cb3 = effective_category_budgets(bf3)
        ca3 = exp.groupby("category")["amount_eur"].sum()
        for c in ca3.index:
            b = float(cb3.get(c, 0))
            if b <= 0:
                continue
            a = float(ca3.get(c, 0))
            pct = progress_ratio(a, b)
            st.markdown(f"**{c}** — {fmt(a, DC, rates)} of {fmt(b, DC, rates)} ({pct*100:.0f}%)")
            st.progress(pct)

# Fun money (current calendar month, regardless of the selected period) — personal
settings_dash = st.session_state.settings
fun_allowance = float(settings_dash.get("fun_money") or 0.0)
if not math.isfinite(fun_allowance):
    fun_allowance = 0.0
if personal_view and fun_allowance > 0:
    from utils import fun_spent, DEFAULT_FUN_CATEGORIES
    fun_cats = settings_dash.get("fun_categories") or DEFAULT_FUN_CATEGORIES
    fun_month = fun_spent(dfe, fun_cats, date.today().year, date.today().month)
    bonus = 0.0
    month_key = f"{date.today().year:04d}-{date.today().month:02d}"
    bonuses_map = settings_dash.get("fun_bonuses") or {}
    if month_key in bonuses_map:
        bonus = float(bonuses_map[month_key] or 0.0)
    elif settings_dash.get("fun_bonus_month") == month_key:
        bonus = float(settings_dash.get("fun_bonus_amount") or 0.0)
    if not math.isfinite(bonus):
        bonus = 0.0
    allowance = fun_allowance + bonus
    fpct = progress_ratio(fun_month, allowance)
    st.subheader("Fun money")
    bonus_str = f" · incl. +€{bonus:.0f} milestone bonus" if bonus > 0 else ""
    st.markdown(f"**{fmt(fun_month, DC, rates)}** of {fmt(allowance, DC, rates)} "
                f"({fpct*100:.0f}%{bonus_str})")
    st.progress(fpct)

# Charts row 1
r1a, r1b = st.columns(2)
with r1a:
    st.subheader("Spending by category")
    if not exp.empty:
        ct  = exp.groupby("category")["amount_eur"].sum().reset_index()
        ct["d"] = ct["amount_eur"].apply(lambda x: to_display(x, DC, rates))
        fig = px.pie(ct, values="d", names="category", hole=0.45,
                     color_discrete_sequence=CHART_COLORS)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("No expenses for this period.")

with r1b:
    if personal_view:
        st.subheader("Budget vs actual")
        if not exp.empty:
            ac = exp.groupby("category")["amount_eur"].sum().reset_index().rename(
                columns={"amount_eur": "ae"})
            if not dfb.empty:
                bf2 = dfb[dfb["year"] == sy]
                if sm > 0: bf2 = bf2[bf2["month"] == sm]
                bc = pd.DataFrame(
                    [(c, v) for c, v in effective_category_budgets(bf2).items()],
                    columns=["category", "budgeted_eur"])
                mg  = ac.merge(bc, on="category", how="outer").fillna(0)
            else:
                mg = ac.copy(); mg["budgeted_eur"] = 0
            mg["status"] = mg.apply(
                lambda r: "Over budget" if r["budgeted_eur"] > 0 and r["ae"] > r["budgeted_eur"]
                else ("Near limit" if r["budgeted_eur"] > 0 and r["ae"] >= r["budgeted_eur"] * NEAR_LIMIT_THRESHOLD
                      else "On track"), axis=1)
            cmap = {"Over budget": C_NEG, "Near limit": C_WARN, "On track": C_POS}
            fig  = go.Figure()
            fig.add_trace(go.Bar(name="Budget", x=mg["category"],
                                 y=mg["budgeted_eur"].apply(lambda x: to_display(x, DC, rates)),
                                 marker_color=C_PRIMARY, opacity=0.45))
            for st2, col2 in cmap.items():
                sub = mg[mg["status"] == st2]
                if not sub.empty:
                    fig.add_trace(go.Bar(name=st2, x=sub["category"],
                                         y=sub["ae"].apply(lambda x: to_display(x, DC, rates)),
                                         marker_color=col2, opacity=0.9))
            fig.update_layout(barmode="group", plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)",
                              margin=dict(t=0,b=0),
                              legend=dict(orientation="h", y=1.08),
                              xaxis_tickangle=-30, yaxis_title=SYM)
            st.plotly_chart(fig, width="stretch")
    else:
        st.caption("Budget vs actual is personal — switch to Personal mode to see it.")

# Monthly trends (personal — mixes income/savings with expenses)
if personal_view:
    st.subheader("Monthly trends")
    def mv(df, col, m):
        if df.empty: return 0.0
        return float(df[(df["date"].dt.year == sy) & (df["date"].dt.month == m)][col].sum())

    trnd = pd.DataFrame([{
        "Month":    calendar.month_abbr[m],
        "Income":   to_display(mv(dfi, "actual_eur", m),    DC, rates),
        "Expenses": to_display(mv(dfe, "amount_eur", m),    DC, rates),
        "Savings":  to_display(mv(dfs, "deposited_eur", m), DC, rates),
    } for m in range(1, 13)])
    fig = go.Figure()
    for col3, clr, dsh in [("Income", C_POS, "solid"), ("Expenses", C_NEG, "solid"),
                           ("Savings", C_PRIMARY, "dot")]:
        fig.add_trace(go.Scatter(x=trnd["Month"], y=trnd[col3], name=col3,
                                 line=dict(color=clr, width=2.5, dash=dsh), mode="lines+markers"))
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.06), margin=dict(t=20,b=0), yaxis_title=SYM)
    st.plotly_chart(fig, width="stretch")

    # Cumulative net cash flow (personal) — demoted behind an expander
    # (research.md U5) so the default scroll stays scannable.
    with st.expander("Cumulative net cash flow", expanded=False):
        cf = pd.DataFrame([{
            "Month": calendar.month_abbr[m],
            "Net": to_display(mv(dfi, "actual_eur", m) - mv(dfe, "amount_eur", m) - mv(dfs, "deposited_eur", m),
                              DC, rates),
        } for m in range(1, 13)])
        cf["Cumulative"] = cf["Net"].cumsum()
        figc = go.Figure()
        figc.add_trace(go.Scatter(x=cf["Month"], y=cf["Net"], name="Monthly net",
                                  mode="lines+markers", line=dict(color=C_BLUE, width=2)))
        figc.add_trace(go.Scatter(x=cf["Month"], y=cf["Cumulative"], name="Cumulative",
                                  mode="lines+markers", line=dict(color=C_PRIMARY, width=2.5)))
        figc.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                           legend=dict(orientation="h", y=1.1), margin=dict(t=20,b=0),
                           yaxis_title=SYM)
        st.plotly_chart(figc, width="stretch")

    # Savings rate chart (personal)
    r3a, r3b = st.columns(2)
    with r3a:
        st.subheader("Savings rate by month")
        rts = pd.DataFrame([{
            "Month": calendar.month_abbr[m],
            "Rate%": round(mv(dfs, "deposited_eur", m) / mv(dfi, "actual_eur", m) * 100, 1)
                     if mv(dfi, "actual_eur", m) > 0 else 0
        } for m in range(1, 13)])
        fig = px.bar(rts, x="Month", y="Rate%",
                     text=rts["Rate%"].apply(lambda x: f"{x:.1f}%"),
                     color="Rate%", color_continuous_scale=[C_NEG, C_WARN, C_POS],
                     range_color=[0, 30])
        fig.add_hline(y=SAVINGS_TARGET_PCT, line_dash="dash", line_color=C_WARN,
                      annotation_text=f"{SAVINGS_TARGET_PCT}% target")
        fig.add_hline(y=SAVINGS_GOAL_PCT, line_dash="dash", line_color=C_POS,
                      annotation_text=f"{SAVINGS_GOAL_PCT}% goal")
        fig.update_traces(textposition="outside")
        fig.update_layout(coloraxis_showscale=False, plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)", margin=dict(t=20,b=0))
        st.plotly_chart(fig, width="stretch")

    with r3b:
        st.subheader("Savings balance")
        if not svyr.empty:
            sp  = svyr.sort_values("date").copy()
            sp["bd"] = sp["balance_eur"].apply(lambda x: to_display(x, DC, rates))
            fig = px.area(sp, x="date", y="bd", color="goal_name",
                          labels={"bd": f"Balance ({SYM})", "goal_name": "Goal"},
                          color_discrete_sequence=CHART_COLORS)
            fig.update_layout(legend_title_text="", plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No savings data for this year.")

# Top 10 — behind an expander (research.md U5)
if not exp.empty:
    with st.expander("Top 10 largest expenses", expanded=False):
        tp = exp.nlargest(10, "amount_eur")[
            ["date","category","subcategory","description","amount","currency","amount_eur"]
        ].copy()
        tp["date"]   = tp["date"].dt.strftime("%d %b %Y").fillna("")
        tp["Amount"] = tp.apply(lambda r: fmt_row(r["amount_eur"], r["amount"], r["currency"], DC, rates), axis=1)
        st.dataframe(
            tp[["date","category","subcategory","description","Amount"]].rename(
                columns={"date": "Date", "category": "Category",
                         "subcategory": "Subcategory", "description": "Description"}),
            hide_index=True, width="stretch",
            column_config={
                "Date": st.column_config.TextColumn("Date"),
                "Category": st.column_config.TextColumn("Category"),
                "Subcategory": st.column_config.TextColumn("Subcategory"),
                "Description": st.column_config.TextColumn("Description"),
                "Amount": st.column_config.TextColumn("Amount"),
            },
        )
