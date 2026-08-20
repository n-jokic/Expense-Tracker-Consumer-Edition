"""
Log expense page: entry form, searchable history with inline editing, trash & restore.
"""

from datetime import date
import re

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

help_expander("How to log an expense",
              "Choose a category first — the subcategory list will update automatically. "
              "Add a short description so you can search for it later. "
              "Tick 'Recurring' to also save it as a monthly template. "
              "On your phone, use 'Scan a receipt' to photograph the bill — "
              "the app reads it (OCR), guesses the amount/merchant/category, "
              "and you accept, edit, or reject the result.")

# ── Receipt scan (OCR on the server; phone just sends the photo) ─────────────
with st.expander("Scan a receipt (OCR)", icon=":material/photo_camera:"):
    cam_img = st.camera_input("Take a photo of the receipt", key="receipt_cam")
    up_img  = st.file_uploader("Or upload a photo", type=["png","jpg","jpeg"],
                               key="receipt_up")
    image_bytes = None
    if cam_img is not None:
        image_bytes = cam_img.getvalue()
    elif up_img is not None:
        image_bytes = up_img.getvalue()

    if image_bytes is not None:
        result = analyze_receipt(image_bytes, q.expenses(user_id), user_id=user_id)
        if not result["ok"]:
            if result.get("reason") == "ocr_not_installed":
                st.warning("Tesseract isn't installed on the server PC yet. "
                           "Install it once with "
                           "`winget install UB-Mannheim.TesseractOCR` — the app "
                           "detects it automatically (no PATH setup or restart needed).")
            else:
                st.warning("OCR couldn't read that image — try a sharper, "
                           "straighter photo, or enter the expense manually below.")
        else:
            st.success("Text recognised — check the details, then save (or fix anything wrong).")
            with st.expander("Raw OCR text", expanded=False):
                st.code((result["text"] or "")[:500], language=None)

            # Category OUTSIDE the form so changing it rebuilds subcategory options immediately.
            r_cat = st.selectbox(
                "Category", CAT_LIST,
                index=CAT_LIST.index(result["category"])
                if result["category"] in CAT_LIST else 0,
                key="rcpt_cat")
            with st.form("receipt_form"):
                r1, r2 = st.columns(2)
                with r1:
                    r_date = st.date_input("Date", value=date.today(), key="rcpt_date")
                    r_sub  = st.selectbox(
                        "Subcategory", ["—"] + CATEGORIES[r_cat],
                        index=(list(["—"] + CATEGORIES[r_cat]).index(result["subcategory"])
                               if result["subcategory"] in CATEGORIES[r_cat] else 0),
                        key="rcpt_sub")
                with r2:
                    _ocr_amt = float(result["amount"]) if pd.notna(result["amount"]) else 0.0
                    r_amt  = st.number_input(f"Amount ({SYM})", value=_ocr_amt,
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
                    r_save = st.form_submit_button("Save expense", type="primary", width="stretch",
                                                   icon=":material/save:")
                with c_rej:
                    r_rej = st.form_submit_button("Reject", width="stretch", icon=":material/delete:")

            if r_save:
                if not (r_desc.strip() and float(r_amt) > 0):
                    safe_error("Please add a description and an amount before saving.")
                else:
                    _fresh_rcpt = q.expenses(user_id)
                    dup_rcpt = False
                    if not _fresh_rcpt.empty:
                        dup_rcpt = (
                            (_fresh_rcpt["date"].dt.date == r_date)
                            & (_fresh_rcpt["description"] == r_desc.strip())
                            & (_fresh_rcpt["amount_eur"].round(2) == round(to_eur(float(r_amt), DC, rates), 2))
                        ).any()
                    if dup_rcpt:
                        st.toast("Already saved — duplicate prevented.", icon=":material/check:")
                        st.rerun()
                    ae = to_eur(float(r_amt), DC, rates)
                    suggested_cat = result.get("category")
                    conf = float(result.get("confidence") or 0.0)
                    final_sub = r_sub if r_sub != "—" else ""
                    suggested_sub = result.get("subcategory") or ""
                    try:
                        add_expense(user_id, {
                        "date": r_date,
                        "category": r_cat,
                        "subcategory": final_sub,
                        "description": r_desc.strip(),
                        "amount": float(r_amt), "currency": DC, "amount_eur": ae,
                        "recurring": False, "notes": (r_notes or "") + " (scanned receipt)",
                        # ML telemetry: what was suggested and whether it stuck
                        "suggest_source": result.get("source"),
                        "suggest_confidence": conf or None,
                        "suggest_model_version": result.get("model_version"),
                        "suggest_merchant": (result.get("merchant") or "").strip().lower(),
                        "suggest_accepted": (r_cat == suggested_cat) if suggested_cat else None,
                        "suggest_subcategory": suggested_sub or None,
                        "suggest_subcategory_confidence": result.get("subcategory_confidence"),
                        "suggest_subcategory_source": result.get("subcategory_source"),
                        "suggest_subcategory_accepted": (final_sub == suggested_sub) if suggested_sub else None,
                        })
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")
                    else:
                        q.bump_db_version()
                        st.success(f"**{r_desc}** — {fmt_dual(float(r_amt), DC, ae)}",
                                   icon=":material/check:")
                        st.balloons()
                        st.rerun()
            if r_rej:
                st.toast("Receipt discarded — nothing was saved.", icon=":material/delete:")
                st.rerun()

oc1, oc2 = st.columns([3, 1])
with oc1:
    cat = st.selectbox("Category", CAT_LIST, key="exp_cat_outer")
with oc2:
    cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="exp_cur_outer")
sym = get_currency_symbol(cur)

with st.form("exp_form", clear_on_submit=True):
    f1, f2 = st.columns(2)
    with f1:
        exp_date = st.date_input("Date", value=date.today())
        subcat   = st.selectbox("Subcategory", ["—"] + CATEGORIES[cat])
    with f2:
        amount  = st.number_input(f"Amount ({sym})", min_value=0.0,
                                  max_value=MAX_AMOUNT, step=0.50, format="%.2f",
                                  value=0.0)
        is_rec  = st.checkbox("Also save as recurring template")
    desc  = st.text_input("Description *", placeholder="e.g. Lidl weekly shop")
    notes = st.text_input("Notes (optional)")
    saved = st.form_submit_button("Save expense", width="stretch", type="primary",
                                  icon=":material/save:")

if saved:
    if not desc.strip():
        safe_error("Please add a description so you can find this expense later.")
    elif amount <= 0:
        safe_error("Amount must be greater than 0.")
    else:
        rec_id = None
        try:
            _fresh_dup = q.expenses(user_id)
            # T4-002: normalized dedup (reuse bank_import:316 pattern)
            _norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()
            if not _fresh_dup.empty and (
                (_fresh_dup["date"].dt.date == exp_date)
                & (_fresh_dup["description"].apply(_norm) == _norm(desc))
                & (_fresh_dup["amount_eur"].round(2) == round(to_eur(amount, cur, rates), 2))
            ).any():
                st.toast("Already saved — duplicate prevented.", icon=":material/check:")
                st.rerun()
            ae = to_eur(amount, cur, rates)
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
        except Exception as e:
            # If the recurring template was created but the expense save failed,
            # recycle the orphan template (mark it inactive) so it is not left active.
            if rec_id:
                try:
                    update_recurring(user_id, rec_id, {"active": False})
                except Exception:
                    pass
                try:
                    q.bump_db_version()
                except Exception:
                    pass
            st.error(f"Couldn't save: {e}")
        else:
            q.bump_db_version()
            st.success(f"**{desc}** — {fmt_dual(amount, cur, ae)}", icon=":material/check:")
            st.balloons()

# ── Expense history ───────────────────────────────────────────────────────────
st.subheader("Expense history")
df_exp = q.expenses(user_id)

if not df_exp.empty:
    def _reset_hist_page():
        """Jump back to the first page whenever filters or page size change."""
        st.session_state["exp_hist_page"] = 0

    sc1, sc2, sc3 = st.columns([3, 2, 2])
    with sc1: srch = st.text_input("Search", placeholder="Search description...", key="exp_srch",
                                   on_change=_reset_hist_page)
    with sc2: catf = st.multiselect("Category filter", CAT_LIST, key="exp_catf",
                                    on_change=_reset_hist_page)
    with sc3: curf = st.multiselect("Currency filter", list(SUPPORTED_CURRENCIES.keys()), key="exp_curf",
                                    on_change=_reset_hist_page)

    psz1, psz2 = st.columns([1, 3])
    with psz1:
        page_size = st.selectbox("Rows per page", [25, 50, 100], index=1,
                                 key="exp_hist_size", on_change=_reset_hist_page)

    v = df_exp.sort_values("date", ascending=False).copy()
    if srch: v = v[v["description"].str.contains(srch, case=False, na=False)]
    if catf: v = v[v["category"].isin(catf)]
    if curf: v = v[v["currency"].isin(curf)]

    # ── Pagination: page state, clamping, nav controls ───────────────────────
    total = len(v)
    page = st.session_state.get("exp_hist_page", 0)
    max_page = (total - 1) // page_size if total else 0
    if page > max_page:                      # clamp if the list shrank (e.g. after a delete)
        page = max_page
    st.session_state["exp_hist_page"] = page

    start = page * page_size
    end = min(start + page_size, total)

    nv1, nv2, nv3 = st.columns([1, 2, 1], vertical_alignment="center")
    with nv1:
        prev_clicked = st.button(":material/chevron_left: Newer",
                                 key="exp_hist_prev", disabled=(page <= 0),
                                 width="stretch")
    with nv2:
        if total:
            st.caption(f"Showing {start + 1}–{end} of {total} — "
                       "edit cells below, tick Trash to trash.")
        else:
            st.caption("Showing 0 of 0 — no matching rows.")
    with nv3:
        next_clicked = st.button("Older :material/chevron_right:",
                                 key="exp_hist_next", disabled=(page >= max_page),
                                 width="stretch")

    if prev_clicked:
        st.session_state["exp_hist_page"] = max(0, page - 1)
        st.rerun()
    if next_clicked:
        st.session_state["exp_hist_page"] = min(max_page, page + 1)
        st.rerun()

    # ── Inline editor (edit or trash directly in the table) ──────────────────
    edit_df = v.iloc[start:end].copy()
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
        key=f"exp_editor_{page}",
        num_rows="fixed",
        hide_index=True,
        column_config={
            "id": None,
            "date": st.column_config.DateColumn("Date"),
            "category": st.column_config.SelectboxColumn("Category", options=CAT_LIST),
            # T4-001: keep broad options for display but whitelist is enforced on save (per CATEGORIES[cat])
            "subcategory": st.column_config.SelectboxColumn("Subcategory", options=["—"] + ALL_SUBCATS),
            "description": st.column_config.TextColumn("Description"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "currency": st.column_config.SelectboxColumn("Currency",
                                                         options=list(SUPPORTED_CURRENCIES.keys())),
            "notes": st.column_config.TextColumn("Notes"),
            "trash": st.column_config.CheckboxColumn("Trash", default=False),
        },
    )

    c_save, c_trash = st.columns(2)
    with c_save:
        save_changes = st.button("Save changes", type="primary", width="stretch",
                                 icon=":material/save:")
    with c_trash:
        trash_selected = st.button("Move ticked rows to trash", type="secondary",
                                   width="stretch", icon=":material/delete:")

    if save_changes:
        changed = 0
        rejected = 0
        save_error = None
        for _, row in edited.iterrows():
            rid  = str(row["id"])
            orig = df_exp[df_exp["id"] == rid]
            if orig.empty:
                continue
            orig = orig.iloc[0]
            # T4-004: NaN → '' coercion before diff (like bank_import:351) + str(nan) guard
            # Work on a coerced copy so NaN/— never reaches DB / breaks comparisons
            coerced = dict(row)
            for _c in ("subcategory", "notes", "category", "currency"):
                v = coerced.get(_c)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    coerced[_c] = ""
                else:
                    # guard "nan" string leaks
                    sv = str(v).strip()
                    coerced[_c] = "" if sv.lower() == "nan" else sv
            # description is special: must not become "nan", empty is rejected
            dv = coerced.get("description")
            if dv is None or (isinstance(dv, float) and pd.isna(dv)):
                coerced["description"] = ""
            else:
                sv = str(dv)
                coerced["description"] = "" if sv.strip().lower() == "nan" else sv
            # T4-001: "—" sentinel → ''
            if coerced.get("subcategory") == "—":
                coerced["subcategory"] = ""
            upd = {}
            for col in ["date","category","subcategory","description","amount","currency","notes"]:
                if not _same(coerced[col], orig[col]):
                    upd[col] = coerced[col]
            if not upd:
                continue
            # T4-001: whitelist guard — invalid sub for final category → ''
            _final_cat = str(upd.get("category", orig["category"]))
            _final_sub = str(upd.get("subcategory", orig.get("subcategory", "") or ""))
            if _final_sub and _final_sub != "—" and _final_sub not in CATEGORIES.get(_final_cat, []):
                upd["subcategory"] = ""
            elif upd.get("subcategory") == "—":
                upd["subcategory"] = ""
            # never write a cleared (NaN) amount or an empty description
            if "amount" in upd and (pd.isna(coerced["amount"]) or float(coerced["amount"]) <= 0):
                rejected += 1
                continue
            if "description" in upd and not str(coerced["description"]).strip():
                rejected += 1
                continue
            if "amount" in upd or "currency" in upd:
                amt = float(upd.get("amount", orig["amount"]))
                cur2 = str(upd.get("currency", orig["currency"]))
                # T4-004 currency was already de-NaNed above; extra guard
                if not cur2 or cur2.lower() == "nan":
                    cur2 = "EUR"
                    upd["currency"] = cur2
                upd["amount_eur"] = to_eur(amt, cur2, rates)
            try:
                update_expense(user_id, rid, upd)
            except Exception as e:
                save_error = str(e)
                break
            changed += 1
        if save_error:
            st.error(f"Couldn't save: {save_error}")
        elif changed:
            q.bump_db_version()
            st.toast(f"{changed} row(s) updated", icon=":material/check:")
            st.rerun()
        elif rejected:
            safe_error(f"{rejected} row(s) not saved — amount/description must not be empty.")
        else:
            st.caption("No changes detected.")

    if trash_selected:
        removed = 0
        trash_error = None
        for _, row in edited.iterrows():
            if bool(row["trash"]):
                try:
                    soft_delete_expense(user_id, str(row["id"]))
                except Exception as e:
                    trash_error = str(e)
                    break
                removed += 1
        if trash_error:
            st.error(f"Couldn't save: {trash_error}")
        elif removed:
            q.bump_db_version()
            st.toast(f"{removed} row(s) moved to trash — you can restore them below.", icon=":material/delete:")
            st.rerun()
        else:
            st.caption("Tick the Trash checkbox on the rows you want to trash.")

    # Restore deleted
    df_deleted = q.expenses(user_id, include_deleted=True)
    df_deleted = df_deleted[df_deleted["is_deleted"] == True]
    if not df_deleted.empty:
        with st.expander(f"Recently deleted ({len(df_deleted)})", icon=":material/delete:"):
            for _, row in df_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"{row['description']} — {row['category']}")
                with rc2: st.write(fmt_row(row["amount_eur"], row["amount"], row["currency"], DC, rates))
                with rc3:
                    if st.button("Restore", key=f"rst_{row['id']}", width="stretch",
                                 icon=":material/undo:"):
                        try:
                            restore_expense(user_id, row["id"])
                        except Exception as e:
                            st.error(f"Couldn't save: {e}")
                        else:
                            q.bump_db_version()
                            st.toast("Expense restored!", icon=":material/undo:")
                            st.rerun()

    with st.expander("Export", icon=":material/download:"):
        st.download_button("Download expenses.xlsx", data=to_excel(df_exp),
                           file_name="expenses.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:")
else:
    st.caption("No expenses yet — add your first one above.")
