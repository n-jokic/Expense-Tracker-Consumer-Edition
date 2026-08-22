"""
Settings page: currency & rates, notifications, account, data export/backup,
sync. (Budgets live on the Budgets page; fun money on the Rewards page.)
"""

import io
import json
import os
import zipfile

import pandas as pd
import streamlit as st

import queries as q
from forecasting import ml_status_line
from db import (
    BACKUP_DIR,
    update_user_display_name, delete_user_account, backup_db,
    create_pairing_device, get_devices, revoke_device,
    get_sync_conflicts, resolve_sync_conflict, apply_record_fields,
    get_household_by_member, get_earned_milestone_ids,
    list_ml_models, activate_ml_model, get_active_ml_model,
    TAXONOMY_RESERVED_CATEGORY, ensure_user_taxonomy_seeded,
    get_user_taxonomy, upsert_user_category, rename_user_category,
    soft_delete_user_category, remap_user_category,
    can_delete_user_category, reorder_user_categories,
)
from auth import change_password, logout
from notifications import render_notification_settings
from app_pages.settings_ai import render_ai_settings
from rates import refresh_rates_if_due
from utils import (
    SUPPORTED_CURRENCIES, get_currency_symbol,
    safe_error, to_excel,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
display_name = st.session_state.display_name

st.title(":material/settings: Settings")

tab_cur, tab_notif, tab_acct, tab_data, tab_cats, tab_sync, tab_ml = st.tabs(
    [":material/currency_exchange: Currency",
     ":material/notifications: Notifications",
     ":material/manage_accounts: Account",
     ":material/database: Data",
     ":material/category: Categories",
     ":material/sync: Sync",
     ":material/psychology: ML"]
)

with st.container(horizontal=True):
    st.caption("Budgets and fun money live on their own pages now:")
    st.page_link("app_pages/budgets.py", label="Budgets", icon=":material/savings:")
    st.page_link("app_pages/rewards.py", label="Rewards & badges",
                 icon=":material/workspace_premium:")

# ── Confirmation dialogs (defined BEFORE their call sites — the page script
# runs top-to-bottom, so a dialog called earlier than its def would NameError) ──

@st.dialog("Revoke device?")
def revoke_device_dialog(uid: int, device_id: str, name: str):
    st.write(f"Revoke access for **{name}**? The phone will need to pair again.")
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        if st.button("Cancel", width="stretch"):
            st.rerun()
        if st.button("Revoke", type="primary", width="stretch"):
            try:
                revoke_device(uid, device_id)
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            st.toast("Device revoked.", icon=":material/lock:")
            st.rerun()


@st.dialog("Delete category with data?")
def remap_delete_dialog(uid: int, cat: str, info: dict, remaining: list):
    """#16 force-remap: the category is in use — move its rows first."""
    st.write(
        f"**{cat}** is used by {info['expense_count']} transactions, "
        f"{info['recurring_count']} recurring templates and "
        f"{info['budget_count']} budgets. Choose where they move:")
    others = [c for c in remaining if c != cat]
    if not others:
        st.error("No other category exists to move data into.")
        return
    target = st.selectbox("Move to:", others)
    _, hc2 = st.columns(2)
    if hc2.button("Move & delete", type="primary",
                  icon=":material/move_down:", width="stretch"):
        try:
            remap_user_category(uid, cat, target)
            q.bump_db_version()
            st.toast(f"Moved everything to **{target}** and removed "
                     f"**{cat}**.", icon=":material/check:")
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't remap: {e}")


# ── Currency tab ──────────────────────────────────────────────────────────────
with tab_cur:
    st.subheader(":material/currency_exchange: Currency & exchange rates")
    st.caption("Amounts are stored in EUR; these rates convert them for display. "
               "They refresh automatically on login when older than 3 days.")
    with st.form("cur_form"):
        dc_default = settings.get("default_currency","EUR")
        dc2 = st.selectbox("Default display currency",
                            list(SUPPORTED_CURRENCIES.keys()),
                            index=list(SUPPORTED_CURRENCIES.keys()).index(dc_default)
                            if dc_default in SUPPORTED_CURRENCIES else 0)
        st.markdown("**Rates (1 EUR = ?)**")
        new_rates = {}
        for c in [c for c in SUPPORTED_CURRENCIES if c != "EUR"]:
            new_rates[c] = st.number_input(
                f"1 EUR = ? {c} ({get_currency_symbol(c)})",
                value=max(float(rates.get(c, 1.0)), 0.0001),
                step=0.01, format="%.4f",
                min_value=0.0001)
        if st.form_submit_button("Save", type="primary", icon=":material/save:"):
            bad = [c for c, v in new_rates.items()
                   if not (v > 0 and v == v)]  # zero, negative, or NaN
            if bad:
                st.error("❌ Exchange rates must be positive numbers greater "
                         "than zero. Fix the highlighted rates and save again.")
            else:
                try:
                    q.save_settings(user_id, {"default_currency": dc2, "currency_rates": new_rates})
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    # Let the sidebar selectbox re-initialise from the new default
                    # (its keyed widget state would otherwise keep the old value).
                    st.session_state.pop("dc_sidebar", None)
                    st.success("✅ Saved — rates updated for every page.")
                    st.rerun()

    last = settings.get("rates_updated_at")
    if last is not None:
        try:
            last_str = pd.Timestamp(last).strftime("%d %b %Y %H:%M")
        except Exception:
            last_str = str(last)
        st.caption(f":material/schedule: Rates last updated from the live API: **{last_str}**")
    else:
        st.caption(":material/schedule: Rates never fetched from the live API — using built-in defaults.")

    if st.button("Refresh rates now", icon=":material/refresh:", width="content",
                 key="refresh_rates_btn"):
        new_settings, ok = refresh_rates_if_due(user_id, st.session_state.settings, force=True)
        if ok:
            got = new_settings.get("currency_rates") or {}
            st.success(f"✅ Rates refreshed! 1 EUR = {float(got.get('RSD', 0)):,.2f} din")
            st.rerun()
        else:
            st.error("😕 Couldn't reach the rate service — keeping your last known rates. "
                     "Check your internet connection and try again.")

# ── Notifications tab ─────────────────────────────────────────────────────────
with tab_notif:
    render_notification_settings(user_id, settings)
    render_ai_settings(user_id, settings)

with tab_ml:
    # ── ML-01: explicit EMPTY / CANDIDATE / ACTIVE / version-history states ──
    st.subheader(":material/psychology: Evaluated ML models")
    from db import deactivate_ml_model, discard_ml_model_version

    def _fmt_metrics(metrics: dict) -> str:
        parts = []
        for k, v in (metrics or {}).items():
            if isinstance(v, (int, float)) and 0.0 <= v <= 1.0 and k != "auto_threshold":
                parts.append(f"{k} {v:.0%}")
            elif isinstance(v, (int, float)):
                parts.append(f"{k} {v:.2f}")
            else:
                parts.append(f"{k} {v}")
        return " · ".join(parts) if parts else "no metrics"

    models = list_ml_models(user_id)
    # #23: how many expenses carry a label right now — feeds the plain-language
    # status line so users know exactly what stands between them and ML.
    _exp_df = q.expenses(user_id)
    _labelled = (0 if _exp_df is None or _exp_df.empty
                 else int(_exp_df["category"].notna().sum()))
    if not models:
        # ── EMPTY state ──────────────────────────────────────────────────────
        st.info(
            "No trained model yet. Once you have logged a handful of "
            "expenses, the app trains a **candidate** text classifier on "
            "your own categories automatically — it never goes live on its "
            "own. You review and activate it here.")
        st.caption(ml_status_line(None, _labelled))
    else:
        by_name: dict[str, list] = {}
        for m in models:
            by_name.setdefault(m.name, []).append(m)
        for name in sorted(by_name):
            versions = sorted(by_name[name], key=lambda m: m.version)
            active = get_active_ml_model(user_id, name)
            newest = versions[-1]
            has_candidate = (active is None) or (newest.version != active.version)

            if active is not None:
                # ── ACTIVE state ────────────────────────────────────────────
                stale_fp = active.dataset_fingerprint != newest.dataset_fingerprint
                status = (":green-badge[Active]" if not stale_fp
                          else ":orange-badge[Active — your data changed since training]")
                st.markdown(
                    f"**{name}** v{active.version} · {status} · "
                    f"{_fmt_metrics(active.metrics)} · trained on "
                    f"{active.trained_rows} rows")
                st.caption(ml_status_line(active.version, _labelled))
                hc1, hc2, _ = st.columns([1, 1, 3])
                with hc1:
                    if st.button("Deactivate", key=f"ml_deactivate_{name}",
                                 icon=":material/pause_circle:",
                                 help="Turn suggestions off. Artifacts and history are kept."):
                        deactivate_ml_model(user_id, name)
                        st.toast("Model deactivated — nothing was deleted.",
                                 icon=":material/pause_circle:")
                        st.rerun()

            if has_candidate:
                # ── CANDIDATE state: newest evaluated version awaits review ─
                cand_status = ("New candidate awaiting your review" if active is None
                               else f"Candidate v{newest.version} — newer than the active v{active.version}")
                with st.container(border=True):
                    st.markdown(
                        f":material/science: **Candidate** {name} "
                        f"v{newest.version} — {cand_status}")
                    st.caption(f"{_fmt_metrics(newest.metrics)} · trained on "
                               f"{newest.trained_rows} rows")
                    cc1, cc2, _rest = st.columns([1, 1, 3])
                    with cc1:
                        if st.button("Activate", key=f"ml_activate_new_{name}",
                                     type="primary", icon=":material/check_circle:"):
                            activate_ml_model(user_id, name, newest.version)
                            st.success(f"{name} v{newest.version} activated.")
                            st.rerun()
                    with cc2:
                        if st.button("Discard", key=f"ml_discard_new_{name}",
                                     icon=":material/delete:",
                                     help="Remove this candidate only. The active model and other versions are untouched."):
                            try:
                                discard_ml_model_version(user_id, name,
                                                         newest.version)
                            except ValueError as ve:
                                st.error(str(ve))
                            else:
                                st.toast("Candidate discarded.", icon=":material/delete:")
                                st.rerun()

            # ── Version history (multiple-version state) ────────────────────
            if len(versions) > 1 or active is None:
                labels = [f"v{m.version} — {_fmt_metrics(m.metrics)} — "
                          f"{m.trained_rows} rows"
                          + (" · active" if active and m.version == active.version else "")
                          for m in versions]
                idx_active = next((i for i, m in enumerate(versions)
                                   if active and m.version == active.version), 0)
                selected = st.selectbox(f"{name} version history", labels,
                                        index=idx_active,
                                        key=f"ml_version_{name}")
                chosen = versions[labels.index(selected)]
                b1, b2, _ = st.columns([1, 1, 3])
                with b1:
                    if st.button(f"Activate selected",
                                 key=f"ml_activate_{name}",
                                 disabled=bool(active and chosen.version == active.version)):
                        activate_ml_model(user_id, name, chosen.version)
                        st.success(f"{name} v{chosen.version} activated.")
                        st.rerun()
                with b2:
                    if st.button("Discard selected", key=f"ml_discard_hist_{name}",
                                 disabled=bool(active and chosen.version == active.version),
                                 help="Delete this non-active candidate version."):
                        try:
                            discard_ml_model_version(user_id, name, chosen.version)
                        except ValueError as ve:
                            st.error(str(ve))
                        else:
                            st.rerun()
            st.divider()

    st.caption("Training runs automatically on your labelled expenses and "
               "only ever registers a candidate here — activation is always "
               "your explicit choice. Any category correction produces a new "
               "candidate; the previously active model keeps serving until "
               "you switch.")

# ── Account tab ───────────────────────────────────────────────────────────────
with tab_acct:
    st.subheader(":material/manage_accounts: Account")

    with st.form("display_name_form"):
        new_name = st.text_input("Display name", value=display_name)
        if st.form_submit_button("Update name", type="primary", icon=":material/save:"):
            if new_name.strip():
                try:
                    update_user_display_name(user_id, new_name.strip())
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    st.session_state.display_name = new_name.strip()
                    # Household member lists / combined views cache display names.
                    q.bump_db_version()
                    st.success("✅ Name updated!")
                    st.rerun()

    st.subheader("Change password")
    with st.form("pw_form"):
        old_pw  = st.text_input("Current password", type="password")
        new_pw  = st.text_input("New password", type="password",
                                placeholder="min. 8 chars, one number")
        conf_pw = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Change password", type="primary", icon=":material/lock:"):
            if new_pw != conf_pw:
                safe_error("New passwords don't match.")
            else:
                ok, msg = change_password(user_id, old_pw, new_pw)
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    safe_error(msg)

    st.subheader(":material/warning: Danger zone")
    with st.expander("Delete my account", icon=":material/delete_forever:"):
        st.error("This will permanently delete **all** your data. This cannot be undone.")
        confirm = st.text_input("Type DELETE to confirm")
        if st.button("Delete account permanently", type="secondary"):
            if confirm == "DELETE":
                try:
                    from forecasting import clear_categorizers
                    clear_categorizers()  # drop the cached ML model for this user
                    delete_user_account(user_id)
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    logout()
                    st.rerun()
            else:
                safe_error("Please type DELETE exactly to confirm.")


# ── Categories tab (#16) ─────────────────────────────────────────────────────
with tab_cats:
    st.subheader(":material/category: Your categories")
    st.caption("Your own category registry — every picker, budget and import "
               "across the app reads this list. 'Uncategorized' is the "
               "permanent catch-all and cannot be removed.")

    ensure_user_taxonomy_seeded(user_id)
    cats_eff, cats_dict_eff = q.effective_categories(user_id)

    # ── Add ──
    with st.form("cat_add_form", clear_on_submit=True):
        new_name = st.text_input("New category name",
                                 placeholder="e.g. Coffee fund")
        new_subs = st.text_input("Subcategories (comma-separated, optional)",
                                 placeholder="e.g. Beans, Cafe visits")
        if st.form_submit_button("Add category", type="primary"):
            try:
                upsert_user_category(
                    user_id, new_name,
                    [s.strip() for s in (new_subs or "").split(",")
                     if s.strip()],
                    len(cats_eff) * 1000)
                q.bump_db_version()
                st.toast(f"Added **{new_name.strip()}**.",
                         icon=":material/check:")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    # ── Existing categories: reorder / rename / subcats / delete ──
    for _i, cat in enumerate(cats_eff):
        reserved = cat == TAXONOMY_RESERVED_CATEGORY
        subs_summary = ", ".join(cats_dict_eff.get(cat) or []) or "—"
        row = st.columns([3, 4, 1, 1, 2])
        with row[0]:
            st.markdown(f"**{cat}**" + (" 🔒" if reserved else ""))
        with row[1]:
            st.caption(subs_summary)
        can_move = not reserved
        with row[2]:
            if (can_move and _i > 0
                    and st.button("⬆", key=f"cat_up_{cat}",
                                  help="Move up")):
                seq = cats_eff[:]
                seq[_i - 1], seq[_i] = seq[_i], seq[_i - 1]
                reorder_user_categories(user_id, seq)
                q.bump_db_version()
                st.rerun()
        with row[3]:
            if (can_move and _i < len(cats_eff) - 1
                    and st.button("⬇", key=f"cat_down_{cat}",
                                  help="Move down")):
                seq = cats_eff[:]
                seq[_i + 1], seq[_i] = seq[_i], seq[_i + 1]
                reorder_user_categories(user_id, seq)
                q.bump_db_version()
                st.rerun()
        with row[4]:
            info = can_delete_user_category(user_id, cat)
            in_use = (info["expense_count"] + info["recurring_count"]
                      + info["budget_count"])
            if not reserved:
                if st.button("Rename", key=f"cat_ren_{cat}"):
                    st.session_state["cat_rename_open"] = cat
                if in_use:
                    if st.button("Delete", key=f"cat_del_{cat}",
                                 help=f"{in_use} rows use this — you'll "
                                      "choose where they move"):
                        remap_delete_dialog(user_id, cat, info, cats_eff)
                elif st.button("Delete", key=f"cat_del_{cat}",
                               help="No data uses this category"):
                    soft_delete_user_category(user_id, cat)
                    q.bump_db_version()
                    st.toast(f"Removed **{cat}**.", icon=":material/delete:")
                    st.rerun()

        # inline rename + subcategory editor for the opened category
        if st.session_state.get("cat_rename_open") == cat:
            with st.form(f"cat_edit_form_{cat}"):
                ren = st.text_input("Name", value=cat)
                subs_new = st.text_input("Subcategories (comma-separated)",
                                          value=subs_summary)
                ok_btn = st.form_submit_button("Save changes",
                                               icon=":material/save:")
                cancel = st.form_submit_button("Cancel")
                if ok_btn:
                    try:
                        target = ren.strip()
                        if target != cat:
                            rename_user_category(user_id, cat, target)
                        upsert_user_category(
                            user_id, target,
                            [s.strip() for s in subs_new.split(",")
                             if s.strip()],
                            int(cats_eff.index(cat)) * 1000)
                        q.bump_db_version()
                        st.session_state.pop("cat_rename_open", None)
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                if cancel:
                    st.session_state.pop("cat_rename_open", None)
                    st.rerun()

# ── Data tab ──────────────────────────────────────────────────────────────────
with tab_data:
    st.subheader(":material/download: Export your data")
    st.caption("Download your data as Excel files. Back these up to Google Drive "
               "or OneDrive regularly.")

    def _jsonify(df: pd.DataFrame) -> pd.DataFrame:
        """Convert dict/list cells to JSON strings so Excel can store them."""
        out = df.copy()
        for col in out.columns:
            if out[col].dtype == object:
                out[col] = out[col].map(
                    lambda v: json.dumps(v, default=str)
                    if isinstance(v, (dict, list)) else v)
        return out

    exports = {
        "expenses":       q.expenses(user_id, include_deleted=True),
        "income":         q.income(user_id, include_deleted=True),
        "savings":        q.savings(user_id, include_deleted=True),
        "term_deposits":  q.savings_accounts(user_id, include_deleted=True),
        "budgets":        q.budgets(user_id),
        "recurring":      q.recurring(user_id),
        "big_purchases":  q.big_purchases(user_id),
        "loans":          q.loans(user_id),
        "holdings":       q.holdings(user_id),
        "holding_prices": q.holding_prices(user_id),
        "audit_log":      q.audit(user_id, limit=10000),
    }
    _s = dict(st.session_state.settings)
    # Never export credentials: the settings sheet must not leak the SMTP
    # password ciphertext, user, the alert email address, the GitHub
    # backup token, or the AI API key.
    for _k in ("smtp_password_enc", "smtp_user", "alert_email", "smtp_host",
               "smtp_port", "gh_token_enc", "ai_api_key_enc"):
        _s.pop(_k, None)
    exports["settings"] = pd.DataFrame(
        [{k: (json.dumps(v, default=str) if isinstance(v, (list, dict)) else v)
          for k, v in _s.items()}])
    hh = get_household_by_member(user_id)
    if hh:
        # The invite code is a shared membership secret — never in exports.
        hh = {k: v for k, v in hh.items() if k != "invite_code"}
        exports["household"] = pd.DataFrame([hh])
    devs = get_devices(user_id)
    if devs:
        exports["devices"] = pd.DataFrame(devs)
    mids = sorted(get_earned_milestone_ids(user_id))
    if mids:
        exports["milestones"] = pd.DataFrame({"milestone_id": mids})
    conf = get_sync_conflicts(user_id, resolved=False)
    if conf:
        exports["sync_conflicts"] = pd.DataFrame(conf)

    def _export_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for key, dfx in exports.items():
                if dfx is None or dfx.empty:
                    continue
                zf.writestr(f"{key}.xlsx", to_excel(_jsonify(dfx)))
        return buf.getvalue()

    st.download_button(
        "Everything (zip)", icon=":material/download:", data=_export_zip(),
        file_name="expense_tracker_export.zip", mime="application/zip",
        key="dl_all", width="stretch",
    )

    data_map = {k: v for k, v in exports.items() if v is not None and not v.empty}
    cols = st.columns(3)
    for i, (key, df_d) in enumerate(data_map.items()):
        with cols[i % 3]:
            st.download_button(
                key.replace("_", " ").capitalize(), icon=":material/download:",
                data=to_excel(_jsonify(df_d)),
                file_name=f"{key}_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_{key}", width="stretch",
            )

    st.subheader(":material/backup: Database backup")
    st.caption("A backup is saved automatically once per day. You can also create one now.")
    marker = os.path.join(BACKUP_DIR, ".last_backup")
    try:
        with open(marker, "r", encoding="utf-8") as f:
            st.caption(f"Last automatic backup: **{f.read().strip()}**")
    except OSError:
        pass
    if st.button("Back up database now", icon=":material/download:"):
        path = backup_db(force=True)
        if path:
            st.success(f"✅ Backup saved to `{path}`")
        else:
            st.info("Backups are only available with the local SQLite database.")

    st.subheader(":material/cloud_upload: GitHub backup (free)")
    st.caption("Upload the **encrypted** database to a private GitHub repository. "
               "Nothing readable is ever uploaded — see the README for setup.")
    with st.form("gh_backup_form"):
        gh_on = st.toggle("Back up automatically once a day",
                          value=bool(settings.get("gh_backup_enabled")))
        gh_repo = st.text_input("Repository", value=settings.get("gh_repo") or "",
                                placeholder="owner/my-private-backup")
        gh_token = st.text_input("GitHub token (fine-grained PAT)",
                                 type="password",
                                 placeholder="Leave blank to keep the existing token")
        gh_ret = st.number_input("Keep backups for (days)", min_value=1, max_value=90,
                                 value=int(settings.get("gh_retention_days") or 14),
                                 step=1)
        c_save, c_run = st.columns(2)
        with c_save:
            saved_gh = st.form_submit_button("Save configuration", type="primary",
                                             icon=":material/save:", width="stretch")
        with c_run:
            run_gh = st.form_submit_button("Back up to GitHub now",
                                           icon=":material/cloud_upload:",
                                           width="stretch")

    if saved_gh:
        updates = {"gh_backup_enabled": bool(gh_on),
                   "gh_repo": gh_repo.strip(),
                   "gh_retention_days": int(gh_ret)}
        if gh_token:
            from crypto import encrypt_str
            updates["gh_token_enc"] = encrypt_str(gh_token)
        try:
            q.save_settings(user_id, updates)
        except Exception as e:
            st.error(f"Couldn't save: {e}")
        else:
            st.success("GitHub backup configuration saved.", icon=":material/check:")
            st.rerun()

    if run_gh:
        import threading
        from github_backup import run_github_backup
        threading.Thread(target=run_github_backup, args=(user_id,),
                         name="gh-backup-manual", daemon=True).start()
        st.info("Backup started in the background — the status appears here "
                "after the next refresh.", icon=":material/hourglass_empty:")
        st.rerun()

    gh_last = settings.get("gh_last_backup_at")
    if gh_last:
        when = gh_last.strftime("%d %b %Y %H:%M") if hasattr(gh_last, "strftime") else str(gh_last)
        if settings.get("gh_last_status") == "ok":
            st.caption(f"Last GitHub backup: **{when}** ✅")
        elif settings.get("gh_last_status") == "error":
            st.error(f"Last GitHub backup failed ({when}): "
                     f"{settings.get('gh_last_error')}")

    # -- #26 E6: local IMAP poller configuration --------------------------
    st.subheader(":material/mark_email_unread: Email inbox (IMAP)")
    st.caption("Let the Log-expense page offer booking candidates from UNSEEN order emails. The app password is stored encrypted on this machine and mail is fetched only when you press the button -- nothing books automatically.")
    with st.form("imap_form"):
        imap_host = st.text_input("IMAP server",
                                  value=settings.get("imap_host") or "",
                                  placeholder="imap.gmail.com")
        imap_user = st.text_input("Account",
                                  value=settings.get("imap_user") or "",
                                  placeholder="you@gmail.com")
        imap_pw = st.text_input("App password", type="password",
                                placeholder="Leave blank to keep the stored one")
        saved_imap = st.form_submit_button(
            "Save inbox settings", icon=":material/save:", width="stretch")
    if saved_imap:
        updates = {"imap_host": imap_host.strip(), "imap_user": imap_user.strip()}
        if imap_pw:
            from crypto import encrypt_str
            updates["imap_app_password_enc"] = encrypt_str(imap_pw)
        try:
            q.save_settings(user_id, updates)
        except Exception as e:
            st.error(f"Could not save: {e}")
        else:
            st.success("Inbox settings saved.", icon=":material/check:")
            st.rerun()


# ── Sync tab (phone pairing + conflicts) ─────────────────────────────────────
with tab_sync:
    st.subheader(":material/sync: Sync & phone pairing")
    st.caption("🧪 **Experimental.** Pair a phone app with this server using a "
               "one-time code. The sync API runs on port 8502 (`python api.py`).")

    st.markdown("**Paired devices**")
    devices = get_devices(user_id)
    if devices:
        for dev in devices:
            d1, d2 = st.columns([3, 1])
            with d1:
                last = dev["last_sync_at"].strftime("%d %b %Y %H:%M") if dev["last_sync_at"] else "never"
                st.write(f"📱 **{dev['name']}** · last sync: {last}")
            with d2:
                if st.button(":material/block: Revoke", key=f"revoke_{dev['id']}",
                             width="stretch"):
                    revoke_device_dialog(user_id, dev["id"], dev["name"])
    else:
        st.caption("No devices paired yet.")

    st.markdown("**Pair a new device**")
    if st.button(":material/add: Generate pairing code", width="stretch"):
        try:
            dev_id, code = create_pairing_device(user_id)
        except Exception as e:
            st.error(f"Couldn't save: {e}")
        else:
            st.session_state.pair_code = code
    pair_code = st.session_state.get("pair_code")
    if pair_code:
        st.success(f"Pairing code: **`{pair_code}`** — valid for 10 minutes. "
                   "Enter it in the phone app (or the /api/pair endpoint).")
        if st.button("Clear code", icon=":material/close:", width="stretch"):
            st.session_state.pop("pair_code", None)
            st.rerun()

    st.subheader(":material/sync_problem: Sync conflicts")
    st.caption("When a record was edited on both the server and a device since "
               "the last sync, it lands here for manual resolution.")
    conflicts = get_sync_conflicts(user_id, resolved=False)
    if not conflicts:
        st.info("No unresolved sync conflicts.", icon=":material/check_circle:")
    for c in conflicts:
        with st.container(border=True):
            st.markdown(f"**{c['table_name']}** · record `{c['record_id']}` · "
                        f"{pd.Timestamp(c['created_at']).strftime('%d %b %H:%M')}")
            cc1, cc2 = st.columns(2)
            with cc1:
                st.markdown("**📱 Device value**")
                st.json(c["device_value"] or {})
            with cc2:
                st.markdown("**🖥️ Server value**")
                st.json(c["server_value"] or {})
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Keep device value", key=f"kdev_{c['id']}", width="stretch"):
                    try:
                        ok = apply_record_fields(user_id, c["table_name"], c["record_id"],
                                                 c["device_value"] or {})
                        if ok:
                            resolve_sync_conflict(user_id, c["id"])
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")
                    else:
                        if ok:
                            q.bump_db_version()
                            st.toast("Device value applied and conflict resolved.", icon=":material/check:")
                            st.rerun()
                        else:
                            st.error("Could not apply the device value (record missing?).")
            with b2:
                if st.button("Keep server value", key=f"ksrv_{c['id']}", width="stretch"):
                    try:
                        resolve_sync_conflict(user_id, c["id"])
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")
                    else:
                        q.bump_db_version()
                        st.toast("Conflict resolved — server value kept.", icon=":material/check:")
                        st.rerun()
