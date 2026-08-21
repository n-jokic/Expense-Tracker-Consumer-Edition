"""
Budgets page: overall monthly budget with live progress + per-category budgets.

Moved out of Settings so budgets are front and center (Plan → Budgets);
the category-pool fun-money controls live on the Rewards page.
"""

import calendar
import math
from datetime import date

import streamlit as st

import queries as q
from db import add_budget, delete_budget
from ui.panel import PanelSpec, panel
from utils import (CATEGORIES, CAT_LIST, SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET,
                   fmt, to_eur, to_display, get_currency_symbol,
                   effective_category_budgets, progress_ratio)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings

st.title(":material/savings: Budgets")


@st.dialog("Delete budget row?")
def budget_delete_dialog(uid: int, bid: int, category: str, subcategory: str):
    label = category + (f" — {subcategory}" if subcategory else "")
    st.write(f"Delete the **{label}** budget row? This cannot be undone.")
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        if st.button("Cancel", width="stretch"):
            st.rerun()
        if st.button("Delete row", type="primary", width="stretch"):
            try:
                delete_budget(uid, bid)
            except Exception as e:
                st.error(f"Couldn't delete budget row: {e}")
                return
            q.bump_db_version()
            st.toast("Budget row deleted.", icon=":material/delete:")
            st.rerun()


_today = date.today()
dfe = q.expenses(user_id)

# ── Overall monthly budget ────────────────────────────────────────────────────
spec = PanelSpec(id="budgets_overall", title="Overall monthly budget",
                 icon=":material/savings:", collapsible=True, default_expanded=True)
expanded, container = panel(spec, user_id=user_id, area="budgets")
if expanded:
    with container:
        _cur_eur_raw = float(settings.get("monthly_budget", 0.0))
        cur_eur = _cur_eur_raw if math.isfinite(_cur_eur_raw) else 0.0

        spent_eur = float(dfe[(dfe["date"].dt.year == _today.year)
                              & (dfe["date"].dt.month == _today.month)]["amount_eur"].sum()) \
            if not dfe.empty else 0.0
        if cur_eur > 0:
            pct = progress_ratio(spent_eur, cur_eur)
            st.markdown(f"**{fmt(spent_eur, DC, rates)}** of {fmt(cur_eur, DC, rates)} "
                        f"({pct * 100:.0f}%) — {fmt(max(cur_eur - spent_eur, 0.0), DC, rates)} left this month")
            st.progress(pct)
        else:
            st.caption("Set an overall monthly budget below to see live progress here.")

        with st.form("overall_bud_form"):
            # The cap and the current value live in the DISPLAY currency, so the
            # cap must be converted too — a fixed 10M cap in EUR would otherwise be
            # smaller than the displayed value for weak currencies and crash the
            # widget (StreamlitValueAboveMaxError).
            ob_cap = to_display(MAX_SAVINGS_TARGET, DC, rates)
            ob_amt = st.number_input(
                f"Total monthly budget ({get_currency_symbol(DC)})",
                min_value=0.0, max_value=ob_cap,
                step=50.0, format="%.2f",
                value=min(to_display(cur_eur, DC, rates), ob_cap))
            ob_eur = to_eur(ob_amt, DC, rates)
            st.caption(f"≈ {fmt(ob_eur, 'EUR', {'EUR': 1.0})} — stored as the EUR base value.")
            if st.form_submit_button("Save budget", type="primary", icon=":material/save:"):
                try:
                    q.save_settings(user_id, {"monthly_budget": round(ob_eur, 4)})
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    st.success(f"✅ Budget set to {fmt(ob_eur, DC, rates)}")
                    st.rerun()

# ── Category budgets ──────────────────────────────────────────────────────────
spec = PanelSpec(id="budgets_category", title="Category budgets",
                 icon=":material/tune:", collapsible=True, default_expanded=True)
expanded, container = panel(spec, user_id=user_id, area="budgets")
if expanded:
    with container:
        st.caption("Per-category caps for a specific month. Alerts and the dashboard "
                   "progress bars compare your spending against them.")
        bcat = st.selectbox("Category", CAT_LIST, key="bud_cat")
        bcur = st.selectbox("Enter in", list(SUPPORTED_CURRENCIES.keys()), key="bud_cur")
        with st.form("cat_bud_form"):
            bc1, bc2, bc3, bc4 = st.columns(4)
            with bc1:
                by = st.number_input("Year", value=_today.year, step=1, format="%d")
            with bc2:
                bm = st.selectbox("Month", range(1, 13),
                                  format_func=lambda x: calendar.month_name[x])
            with bc3:
                bsub = st.selectbox("Subcategory",
                                    ["(entire category)"] + CATEGORIES[bcat])
            with bc4:
                ba = st.number_input(f"Budget ({get_currency_symbol(bcur)})", min_value=0.0,
                                     max_value=MAX_SAVINGS_TARGET, step=10.0, format="%.2f")
            if st.form_submit_button("Save", type="primary", icon=":material/save:"):
                be = to_eur(ba, bcur, rates)
                try:
                    add_budget(user_id, {
                        "year": int(by), "month": int(bm), "category": bcat,
                        "subcategory": bsub if bsub != "(entire category)" else "",
                        "budgeted_eur": be,
                    })
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    q.bump_db_version()
                    st.success("✅ Budget saved")
                    st.rerun()

dfb = q.budgets(user_id)

# ── This month's category progress ────────────────────────────────────────────
if not dfb.empty:
    cur_rows = dfb[(dfb["year"] == _today.year) & (dfb["month"] == _today.month)]
    if not cur_rows.empty:
        spec = PanelSpec(id="budgets_progress", title="This month's category progress",
                         icon=":material/insights:", collapsible=True, default_expanded=True)
        expanded, container = panel(spec, user_id=user_id, area="budgets")
        if expanded:
            with container:
                exp = dfe[(dfe["date"].dt.year == _today.year)
                          & (dfe["date"].dt.month == _today.month)] if not dfe.empty else dfe
                eff_budgets = effective_category_budgets(cur_rows)
                cats_with_sub = set(cur_rows[cur_rows["subcategory"].fillna("").astype(str).str.strip() != ""]
                                    ["category"].unique())
                for _, r in cur_rows.iterrows():
                    if str(r["subcategory"]) == "" and r["category"] in cats_with_sub:
                        continue  # subcategory rows are authoritative for this category
                    _raw = r["subcategory"]
                    try:
                        _is_nan = _raw is not None and _raw != _raw  # NaN != NaN (covers float & numpy)
                    except Exception:
                        _is_nan = False
                    _sub = "" if _raw is None or _is_nan else str(_raw).strip()
                    if _sub.lower() == "nan":
                        _sub = ""
                    if _sub != "":
                        b = float(r["budgeted_eur"])
                    else:
                        b = float(eff_budgets.get(r["category"], r["budgeted_eur"]))
                    if _sub == "":
                        spent = float(exp[exp["category"] == r["category"]]["amount_eur"].sum()) \
                            if not exp.empty else 0.0
                        lbl = str(r["category"])
                    else:
                        spent = float(exp[(exp["category"] == r["category"])
                                          & (exp["subcategory"] == r["subcategory"])]
                                      ["amount_eur"].sum()) if not exp.empty else 0.0
                        lbl = f"{r['category']} › {r['subcategory']}"
                    if b > 0:
                        pct = progress_ratio(spent, b)
                        st.markdown(f"**{lbl}** — {fmt(spent, DC, rates)} of "
                                    f"{fmt(b, DC, rates)} ({pct * 100:.0f}%)")
                        st.progress(pct)

# ── All budget rows (delete) ──────────────────────────────────────────────────
if not dfb.empty:
    spec = PanelSpec(id="budgets_all_rows", title="All budget rows",
                     icon=":material/table_view:", collapsible=True, default_expanded=True)
    expanded, container = panel(spec, user_id=user_id, area="budgets")
    if expanded:
        with container:
            d = dfb.copy()
            d["month"] = d["month"].apply(lambda x: calendar.month_name[int(x)])
            d["Budget"] = d["budgeted_eur"].apply(lambda x: to_display(x, DC, rates))
            sym = get_currency_symbol(DC)
            budget_fmt = f"%,.0f {sym}" if DC in ("RSD", "HUF", "HRK") else f"{sym}%,.2f"
            sel = st.dataframe(
                d[["year", "month", "category", "subcategory", "Budget"]],
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "Budget": st.column_config.NumberColumn("Budget", format=budget_fmt),
                },
            )
            selected = sel.selection.rows
            if selected:
                with st.expander("Delete selected row", icon=":material/delete:"):
                    if st.button("Delete", type="secondary", key="del_bud"):
                        row = dfb.iloc[selected[0]]
                        budget_delete_dialog(user_id, int(row["id"]), row["category"],
                                             row["subcategory"])
            else:
                with st.expander("Delete a budget row", icon=":material/delete:"):
                    st.caption("Select a row in the table above to delete it.")
