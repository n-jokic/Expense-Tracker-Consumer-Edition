"""
Log expense page: entry form, searchable history with inline editing, trash & restore.
"""

from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import add_expense, update_expense, soft_delete_expense, restore_expense, add_recurring
from ocr import analyze_receipt
from utils import (
    CATEGORIES, CAT_LIST, ALL_SUBCATS, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    fmt_row, fmt_dual, to_eur, get_currency_symbol,
    safe_error, help_expander, to_excel,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
SYM     = get_currency_symbol(DC)

st.title("📅 Log expense")
help_expander("How to log an expense",
              "Choose a category first — the subcategory list will update automatically. "
              "Add a short description so you can search for it later. "
              "Tick '🔄 Recurring' to also save it as a monthly template. "
              "On your phone, use '📷 Scan a receipt' to photograph the bill — "
              "the app reads it (OCR), guesses the amount/merchant/category, "
              "and you accept, edit, or reject the result.")

# ── Receipt scan (OCR on the server; phone just sends the photo) ─────────────
with st.expander("📷 Scan a receipt (OCR)"):
    cam_img = st.camera_input("Take a photo of the receipt", key="receipt_cam")
    up_img  = st.file_uploader("…or upload a photo", type=["png","jpg","jpeg"],
                               key="receipt_up")
    image_bytes = None
    if cam_img is not None:
        image_bytes = cam_img.getvalue()
    elif up_img is not None:
        image_bytes = up_img.getvalue()

    if image_bytes is not None:
        result = analyze_receipt(image_bytes, q.expenses(user_id))
        if not result["ok"]:
            st.warning("📷 OCR isn't available on this machine yet. "
                       "Install Tesseract (Windows: `winget install UB-Mannheim.TesseractOCR`) "
                       "and restart the app — see the README for details.")
        else:
            st.success("Text recognised — check the details, then save (or fix anything wrong).")
            with st.expander("Raw OCR text", expanded=False):
                st.code((result["text"] or "")[:500], language=None)

            with st.form("receipt_form"):
                r1, r2 = st.columns(2)
                with r1:
                    r_date = st.date_input("Date", value=date.today(), key="rcpt_date")
                    r_cat  = st.selectbox(
                        "Category", CAT_LIST,
                        index=CAT_LIST.index(result["category"])
                        if result["category"] in CAT_LIST else 0,
                        key="rcpt_cat")
                    r_sub  = st.selectbox(
                        "Subcategory", ["—"] + CATEGORIES[r_cat],
                        index=(list(["—"] + CATEGORIES[r_cat]).index(result["subcategory"])
                               if result["subcategory"] in CATEGORIES[r_cat] else 0),
                        key="rcpt_sub")
                with r2:
                    r_amt  = st.number_input(f"Amount ({SYM})", value=float(result["amount"] or 0.0),
                                             min_value=0.0, max_value=MAX_AMOUNT,
                                             step=0.50, format="%.2f", key="rcpt_amt")
                    r_desc = st.text_input("Description", value=result["merchant"] or "",
                                           key="rcpt_desc")
                r_notes = st.text_input("Notes (optional)", key="rcpt_notes")
                if result["confidence"] and result["confidence"] > 0:
                    st.caption(f"Category suggested by your trained classifier "
                               f"(confidence {result['confidence']:.0%}).")
                c_save, c_rej = st.columns(2)
                with c_save:
                    r_save = st.form_submit_button("✅ Save expense", type="primary", width="stretch")
                with c_rej:
                    r_rej = st.form_submit_button("🗑️ Reject", width="stretch")

            if r_save:
                if not (r_desc.strip() and float(r_amt) > 0):
                    safe_error("Please add a description and an amount before saving.")
                else:
                    ae = to_eur(float(r_amt), DC, rates)
                    add_expense(user_id, {
                        "date": r_date,
                        "category": r_cat,
                        "subcategory": r_sub if r_sub != "—" else "",
                        "description": r_desc.strip(),
                        "amount": float(r_amt), "currency": DC, "amount_eur": ae,
                        "recurring": False, "notes": (r_notes or "") + " (scanned receipt)",
                    })
                    q.bump_db_version()
                    st.success(f"✅ **{r_desc}** — {fmt_dual(float(r_amt), DC, ae)}")
                    st.balloons()
                    st.rerun()
            if r_rej:
                st.toast("Receipt discarded — nothing was saved.", icon="🗑️")
                st.rerun()

oc1, oc2 = st.columns([3, 1])
with oc1:
    cat = st.selectbox("Category", CAT_LIST, key="exp_cat_outer")
with oc2:
    cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="exp_cur_outer")
sym = get_currency_symbol(cur)

with st.form("exp_form", clear_on_submit=False):
    f1, f2 = st.columns(2)
    with f1:
        exp_date = st.date_input("Date", value=date.today())
        subcat   = st.selectbox("Subcategory", ["—"] + CATEGORIES[cat])
    with f2:
        amount  = st.number_input(f"Amount ({sym})", min_value=0.01,
                                  max_value=MAX_AMOUNT, step=0.50, format="%.2f")
        is_rec  = st.checkbox("🔄 Also save as recurring template")
    desc  = st.text_input("Description *", placeholder="e.g. Lidl weekly shop")
    notes = st.text_input("Notes (optional)")
    saved = st.form_submit_button("✅ Save expense", width="stretch", type="primary")

if saved:
    if not desc.strip():
        safe_error("Please add a description so you can find this expense later.")
    else:
        ae = to_eur(amount, cur, rates)
        rec_id = None
        if is_rec:
            rec_id = add_recurring(user_id, {
                "category": cat,
                "subcategory": subcat if subcat != "—" else "",
                "description": desc, "amount": amount,
                "currency": cur, "amount_eur": ae,
                "notes": notes, "active": True,
            })
        add_expense(user_id, {
            "date": exp_date, "category": cat,
            "subcategory": subcat if subcat != "—" else "",
            "description": desc, "amount": amount,
            "currency": cur, "amount_eur": ae,
            "recurring": is_rec, "rec_template_id": rec_id,
            "notes": notes,
        })
        q.bump_db_version()
        st.success(f"✅ **{desc}** — {fmt_dual(amount, cur, ae)}")
        st.balloons()

# ── Expense history ───────────────────────────────────────────────────────────
st.divider()
st.subheader("Expense history")
df_exp = q.expenses(user_id)

if not df_exp.empty:
    sc1, sc2, sc3 = st.columns([3, 2, 2])
    with sc1: srch = st.text_input("🔍 Search", placeholder="Search description...", key="exp_srch")
    with sc2: catf = st.multiselect("Category filter", CAT_LIST, key="exp_catf")
    with sc3: curf = st.multiselect("Currency filter", list(SUPPORTED_CURRENCIES.keys()), key="exp_curf")

    v = df_exp.sort_values("date", ascending=False).copy()
    if srch: v = v[v["description"].str.contains(srch, case=False, na=False)]
    if catf: v = v[v["category"].isin(catf)]
    if curf: v = v[v["currency"].isin(curf)]

    # ── Inline editor (edit or trash directly in the table) ──────────────────
    st.caption(f"{len(v)} matching rows — edit cells below, tick 🗑️ to trash.")
    edit_df = v.head(50).copy()
    edit_df["trash"] = False

    def _same(a, b):
        if pd.isna(a) and pd.isna(b):
            return True
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) == float(b)
        try:
            return pd.Timestamp(a) == pd.Timestamp(b)
        except Exception:
            return str(a) == str(b)

    edited = st.data_editor(
        edit_df[["id","date","category","subcategory","description","amount","currency","notes","trash"]],
        key="exp_editor",
        num_rows="fixed",
        hide_index=True,
        column_config={
            "id": None,
            "date": st.column_config.DateColumn("Date"),
            "category": st.column_config.SelectboxColumn("Category", options=CAT_LIST),
            "subcategory": st.column_config.SelectboxColumn("Subcategory", options=ALL_SUBCATS),
            "description": st.column_config.TextColumn("Description"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "currency": st.column_config.SelectboxColumn("Currency",
                                                         options=list(SUPPORTED_CURRENCIES.keys())),
            "notes": st.column_config.TextColumn("Notes"),
            "trash": st.column_config.CheckboxColumn("🗑️ Trash", default=False),
        },
    )

    c_save, c_trash = st.columns(2)
    with c_save:
        save_changes = st.button("💾 Save changes", type="primary", width="stretch")
    with c_trash:
        trash_selected = st.button("🗑️ Move ticked rows to trash", type="secondary", width="stretch")

    if save_changes:
        changed = 0
        for _, row in edited.iterrows():
            rid  = str(row["id"])
            orig = df_exp[df_exp["id"] == rid]
            if orig.empty:
                continue
            orig = orig.iloc[0]
            upd = {}
            for col in ["date","category","subcategory","description","amount","currency","notes"]:
                if not _same(row[col], orig[col]):
                    upd[col] = row[col]
            if upd:
                if "amount" in upd or "currency" in upd:
                    amt = float(upd.get("amount", orig["amount"]))
                    cur2 = str(upd.get("currency", orig["currency"]))
                    upd["amount_eur"] = to_eur(amt, cur2, rates)
                update_expense(user_id, rid, upd)
                changed += 1
        if changed:
            q.bump_db_version()
            st.toast(f"✅ {changed} row(s) updated", icon="✅")
            st.rerun()
        else:
            st.info("No changes detected.")

    if trash_selected:
        removed = 0
        for _, row in edited.iterrows():
            if bool(row["trash"]):
                soft_delete_expense(user_id, str(row["id"]))
                removed += 1
        if removed:
            q.bump_db_version()
            st.toast(f"{removed} row(s) moved to trash — you can restore them below.", icon="🗑️")
            st.rerun()
        else:
            st.info("Tick the 🗑️ checkbox on the rows you want to trash.")

    # Restore deleted
    df_deleted = q.expenses(user_id, include_deleted=True)
    df_deleted = df_deleted[df_deleted["is_deleted"] == True]
    if not df_deleted.empty:
        with st.expander(f"🗑️ Recently deleted ({len(df_deleted)})"):
            for _, row in df_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"{row['description']} — {row['category']}")
                with rc2: st.write(fmt_row(row["amount_eur"], row["amount"], row["currency"], DC, rates))
                with rc3:
                    if st.button("↩️ Restore", key=f"rst_{row['id']}", width="stretch"):
                        restore_expense(user_id, row["id"])
                        q.bump_db_version()
                        st.toast("Expense restored!", icon="↩️")
                        st.rerun()

    with st.expander("📥 Export"):
        st.download_button("⬇️ Download expenses.xlsx", data=to_excel(df_exp),
                           file_name="expenses.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("No expenses yet — add your first one above 👆")
