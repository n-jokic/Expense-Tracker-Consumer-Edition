# Follow-up QA Cycle — Re-validation of 6 SUSPECTED + 4 Leads
> Generated: 2026-08-20 (follow-up to Final QA Bug Map 2026-08-20)
> Source: adversarial re-validation, 10 independent validators (one per target), ephemeral probes under qa/_tmp
> Scope: Qa registry SUSPECTED 6 (T1-001, T1-003, T2-002, T3-003, T7-005, T8-005) + new-leads 4 (LEAD-001..004)
> Rule: DO NOT MODIFY APPLICATION CODE — validation only

---

## 1. Executive Summary

| Cohort | Targets | Validators | Settled |
|--------|---------|------------|---------|
| SUSPECTED (deferred) | 6 | 6 | 6 (5 returned, 1 provisional manual — agent still running >300s) |
| Leads (UNVALIDATED) | 4 | 4 | 4 |
| **Total** | **10** | **10** | **10** |

### Re-validated verdicts

| ID | Original | Re-validated | Severity | Action |
|----|----------|--------------|----------|--------|
| T1-SHELL-AUTH-001 | SUSPECTED LOW | **NOT-A-BUG** (INFO hardening) | INFO | Close — per-username intentional, home-LAN, dict leak bounded |
| T1-SHELL-AUTH-003 | SUSPECTED LOW | **NOT-A-BUG** (INFO) | INFO | Close — fail-closed, Docker :-false mitigates |
| T2-PERSISTENCE-002 | SUSPECTED LOW | **CONFIRMED LOW (narrow/transient)** | LOW | Keep — RETURNING arbitrary mirror, single-rerun stale only |
| T3-CURRENCY-003 | SUSPECTED LOW | **NOT-A-BUG** (FALSE_POSITIVE) | — | Close — partial wire correctly rejected |
| T7-INTELLIGENCE-005 | SUSPECTED LOW | **CONFIRMED LOW (doc only)** | LOW | Fix caption only — not a leak |
| T8-CONNECTIVITY-005 | SUSPECTED LOW | **NOT-A-BUG** | INFO | Close — per-IP 5/600s correct for LAN |
| LEAD-001 | UNVALIDATED MED | **CONFIRMED LOW** | LOW | Promote to backlog — isolated _eur bypass on CREATE |
| LEAD-002 | UNVALIDATED LOW | **NOT-A-BUG** | LOW | Close — hex precedence self-consistent |
| LEAD-003 | UNVALIDATED LOW | **CONFIRMED LOW** | LOW (MED if US CSV supported) | Promote — US 1,200 →1.2 asymmetry |
| LEAD-004 | UNVALIDATED LOW | **NOT-A-BUG** (infra fixed) | LOW | Close — data/_pytest_tmp restored DB signal; residual github_backup temp |

* T2-002 validator f76db170 CONFIRMED-LOW transient mirror only (db.py:946-951 RETURNING fetchone arbitrary, DB correctly bumped, heals next rerun) — manual provisional confirmed by validator.

**Promoted to remediation backlog (2):** LEAD-001 (isolated _eur), LEAD-003 (US thousands guard).
**Closed as NOT-A-BUG (6):** T1-001, T1-003, T3-003, T8-005, LEAD-002, LEAD-004.
**Doc-only fix (1):** T7-005 caption.
**Narrow transient (1):** T2-002 — defer or fix with RETURNING max/order.

---

## 2. SUSPECTED Re-validation Detail

### T1-SHELL-AUTH-001 — Throttle per-username dict-key leak + naive clock
- **Location:** auth.py:40 _attempts=defaultdict(deque), 57-65 _throttled, 138 throttle_key=f"local|{username}"
- **Original:** SUSPECTED LOW P4 partial
- **Re-validated:** **NOT-A-BUG** — confidence HIGH — validator 715d001d + manual trace
- **Evidence:**
  - Per-username isolation matches invariants G10 (shared-local bucket) + agent instructions/app-shell/auth-and-onboarding.md:157 (intentionally per-user to avoid one user blocking household) and auth.py:45-54 comment explicitly rejects XFF IP bucket.
  - Dict-key leak technically real: _throttled key creation on line 59 (defaultdict) + stale deque never evicted until next hit for that username, no 1000-cap unlike api.py:45. Probe qa/_tmp/probe_t1_throttle.py shows 3 random users leave 3 keys, only accessed key expires after 61s. Cost bounded/process-local/lost on restart; requires millions of unique usernames for ~80MB — home-LAN model (README 0.0.0.0:8501 plain HTTP, compose Caddy 127.0.0.1:8501 loopback, single-user WiFi) out of scope.
  - Naive datetime.now() vs invariants G9 UTC claim is delta-neutral: both ends naive, probe shows 30.0s == 30.0s; DST 1h jump twice/year only shortens one 60s window by 1h — not reliably exploitable.
- **Severity:** INFO hardening only
- **Fix hint (optional):** auth.py:58 datetime.now(timezone.utc) or time.monotonic() + bound like api.py:45-46 LRU eviction — not required under documented threat model.

### T1-SHELL-AUTH-003 — ALLOW_REGISTRATION empty-string blocks secrets fallback
- **Location:** auth.py:68-82 _registration_enabled()
- **Original:** SUSPECTED LOW
- **Re-validated:** **NOT-A-BUG** — confidence HIGH — validator d255d5a7 + two live probes
- **Trace:**
  - Code: val=os.environ.get("ALLOW_REGISTRATION"); if val is None: val=st.secrets.get(...) — empty "" is not None so secrets skipped — code smell confirmed.
  - Security false: return str(val).strip().lower() in ("1","true","yes","on") — "" → False (registration DISABLED, fail-closed). No bypass to True.
  - Docker: compose.yaml:11 ${ALLOW_REGISTRATION:-false} — POSIX :- substitutes for unset OR null/empty → container "false". Host "" → container false. Bare host "" still False. Live auth import probe confirms ""+secrets true → False, unset+true → True.
- **Severity:** INFO UX — operator expecting default-open gets closed instead, not a vuln.
- **Fix hint (optional UX):** if not val or not str(val).strip(): fall through to secrets — not security-required.

### T2-PERSISTENCE-002 — Household bump RETURNING arbitrary row stays diverged
- **Location:** db.py:925-961 bump_data_revision, queries.py:43-96 snapshot coherency
- **Original:** SUSPECTED LOW P2+P6
- **Re-validated:** **CONFIRMED LOW (narrow, transient mirror only)** — confidence HIGH — validator f76db170 + manual trace (provisional confirmed)
- **Causal chain:**
  - ids=[caller] + household members (db.py:933-939). Engine path: UPDATE users SET data_revision=COALESCE+1 WHERE id IN (...) RETURNING data_revision (db.py:946-951) → fetchone()[0] returns arbitrary row among N (SQLite row order unspecified). If household size 2 and revisions were previously synchronized (always bump household together), they stay synchronized (+1 each) → arbitrary equals correct. If they ever diverged (e.g. pre-fix per-user bump), after bump A=oldA+1, B=oldB+1 still differ by same delta; fetchone may return B's value for A's caller.
  - bump_db_version (queries.py:83-96) stores returned rev as _snap_version for that rerun; next q.* in same rerun reuse stale arbitrary. Next rerun re-reads via get_data_revision(uid) (db.py:920) → correct per-user value, so drift is single-rerun only.
- **Evidence:** Code inspection + transaction semantics; no probe needed — SQLite RETURNING multi-row fetchone documented arbitrary.
- **Impact:** Single-rerun stale cache key: household member B may not see A's write until its next rerun (≈ seconds). No data loss, no persistent divergence once next rerun syncs. Household always bumped together now, so initial divergence cannot reoccur unless manual divergence injected.
- **Severity:** LOW — transient mirror, no data loss
- **Fix hint:** RETURNING max(data_revision) or SELECT after UPDATE, or WHERE id=:callerId RETURNING after bumping all; fallback path already correct via get_data_revision(uid).

### T3-CURRENCY-003 — Taxonomy remap partial wire subcategory-only rejection
- **Location:** utils.py:17-113 CATEGORIES + 54-row TAXONOMY_MIGRATION, sync_core.py:345-368 validate_fields taxonomy gate (claimed 175-184 was actually income _recompute, misattribution)
- **Original:** SUSPECTED LOW P3
- **Re-validated:** **NOT-A-BUG — FALSE_POSITIVE** — confidence HIGH — validator 46c47b18, 16 probes
- **Evidence:**
  - 54-row taxonomy verified: all targets in CATEGORIES (12 cats) + ALL_SUBCATS (48) or empty, no orphan.
  - FIELD_SCHEMAS str → validate_fields 345-354: remap_category_subcategory(pair) via _TAXONOMY_LOOKUP.get(..., pass-through) BEFORE whitelist checks against CATEGORIES/ALL_SUBCATS — stale client Housing/Water → Housing & Utilities/Water accepted+rewritten correctly; BOGUS subcategory-only (subcategory=BOGUS) → errors ["unknown subcategory"] → rejected, clean keeps BOGUS but apply_changes surfaces failed[], DB unchanged — never silent persist (3 probes + 3 apply_changes integration).
  - Residual INFO: cross-category pairing (Groceries/Tolls — Tolls in ALL_SUBCATS but belongs to Transport) passes because whitelist is pool-wide, not per-category — intentional design, not currency-relevant, severity INFO not P3.
- **Action:** Close. Cite correct lines sync_core.py:345-354. No fix required; strict pairing could be future hardening via CATEGORIES[cat] check.

### T7-INTELLIGENCE-005 — Ask numeric-only doc overclaim
- **Location:** llm.py:248-253 _sanitize_stat, 327-384 build_data_context, ask.py:5+21, README:375, agent instructions/intelligence/insights-and-llm.md:215,259,263
- **Original:** SUSPECTED LOW docs overclaim
- **Re-validated:** **CONFIRMED (narrowed) LOW — doc bug, NOT a leak** — confidence HIGH — validator 811bbed7
- **Trace:**
  - Sanitizer: _sanitize_stat strips \r/\n → space and caps at 100 chars on every free-text — applied to all.
  - Code ships: top-5 categories (361-364), savings goal_name unbounded uniques but each capped (376-384), loans name (396), bills head(8) descriptions (403), recent head(10) desc+cat (416-426), history last 4 × 200 (447-450). Counts bounded (5/8/10+4), values capped.
  - Docs: ask.py:5-8 header "sanitized snapshot of NUMERIC aggregates (plus your recent transaction descriptions, stripped and capped), never credentials" — ACCURATE. ask.py:21 caption "sanitized snapshot of your numbers — nothing else —" — FALSE (ships names/descs intentionally). README + insights-and-llm contract accurately disclose capped names/descs.
  - Credentials: decrypt_str only to local var/header (llm.py:200-212), never into context; provider badge uses basename only.
- **Severity:** LOW copy bug, no data-leak fix
- **Fix hint:** Change caption to match header/README: "sanitized snapshot of your numbers plus capped names/descriptions — nothing else" — docs-only, no code logic change.

### T8-CONNECTIVITY-005 — Pairing rate limit per-process global clear
- **Location:** api.py:34-53 limiter, db.py:2339-2422 pairing (claimed db:2282 is actually backup guard, misattribution)
- **Original:** SUSPECTED LOW
- **Re-validated:** **NOT-A-BUG** — confidence HIGH — validator 85334f0b
- **Trace:**
  - Per-IP in-memory dict keyed by request.client.host (api.py:44), window 600s max 5, threading.Lock — proven by tests/test_api.py:74-78 (6th from same TestClient →429).
  - "Global clear" is GC bound >1000 distinct IPs (api.py:45-46 _pair_attempts.clear()) — requires 1001 IPs to trigger, not single LAN attacker; on /24 home LAN max 254 hosts impossible. Even triggered, allows only 5 extra guesses against 36^6 ≈2.1B secrets (secrets.choice, db.py:2008) + 10-min expiry + atomic UPDATE WHERE pairing_code single-use.
  - Volatile dict lost on restart — intentional per connectivity/sync-and-household.md:256 TODO, but launcher single uvicorn no workers (api.py:156, compose Caddy 127.0.0.1:8501 only, 8502 LAN experimental) — outside multi-replica threat model.
- **Severity:** INFO hardening (persist to DB/Redis) only if API exposed publicly with workers — out of scope.

---

## 3. Leads Re-validation Detail

### LEAD-001 — Sync create with only amount_eur bypasses recompute (adjacent to T3-001/004)
- **Location:** sync_core.py:146-206 _recompute_derived_eur, 362-405 validate_fields + create_record
- **Observation:** Sync create with only amount_eur (no amount/currency) passes cap and stores verbatim without to_eur recompute
- **Re-validated:** **CONFIRMED LOW** — validator be5b80a3, live probe qa/_tmp/probe_LEAD-001.py
- **Repro:**
  - validate_fields("expenses", {"amount_eur":900000}, rates={}) → clean={"amount_eur":900000}, errors=[] — no recompute because existing=None guard amt is not None fails when amount missing, so _recompute leaves verbatim.
  - apply_changes CREATE rid=probe-eur-only-900k with date/category/desc + amount_eur 900k → DB stored amount=0.0 currency=EUR amount_eur=900000.0 verbatim.
  - Cap correctly enforced: 5_000_000 → errors ["amount_eur must be >0 and <=1e+06"] and apply_changes failed — 5M bypass claim false on that value. UPDATE path is fixed (existing fallback recomputes).
  - Same isolated bypass affects income budgeted_eur/actual_eur, savings deposited_eur, savings_accounts amount_eur.
- **Impact:** Attacker can poison EUR aggregates up to MAX_AMOUNT without controlling FX rate; 900k < 1M so under cap. Requires sync-authenticated paired device.
- **Fix hint:** Fix CREATE recompute: when existing is None and _eur in clean but base is None, either reject isolated _eur (require amount/budgeted/deposited), or drop/recompute to 0 via to_eur(0,EUR). Add to all 4 tables. validate_fields already caps _eur at MAX_AMOUNT but must enforce base-required invariant.

### LEAD-002 — crypto 64-hex precedence UX
- **Location:** crypto.py:39-43 _env_secret, persistence/encryption-and-crypto.md §5
- **Observation:** 64-char hex-like passphrase decoded via bytes.fromhex not sha256 — self-consistent but user expectation mismatch
- **Re-validated:** **NOT-A-BUG** — validator 6d41ad91, live reload probes
- **Trace:** Mechanism bytes.fromhex len 64 → 32 bytes confirmed (ab*32 → 32 bytes, not sha256; z*64 falls through to sha256). Precedence 1) 64-hex 2) base64 urlsafe 3) sha256 documented in module doc (crypto.py:11-13) and encryption-and-crypto.md §5 and pinning test test_crypto.py:135-142. Reload shows master/fernet/sqlcipher deterministic, no data loss; worst case user must reuse same env value. Changing would brick existing DBs.
- **Action:** Keep precedence; close as documented UX, INFO.

### LEAD-003 — US comma thousands 1,200 parses as 1.2 in bank_import vs 1200 in pdf_import
- **Location:** bank_import.py:158-201 _to_numeric_locale (192-193 elif "," in s: replace), pdf_import.py:143-187 _parse_amount_core
- **Re-validated:** **CONFIRMED LOW** (MED if US CSV officially supported) — validator 6c3393c5, live matrix
- **Evidence:**
  - Bank: "1,200" → elif "," path unconditional replace → "1.200" → 1.2 (bank_import _to_numeric_locale). PDF: groups len 3 guard → 1200.0 via _parse_amount_core:171-178, _parse_amount_token, _is_pure_amount_cell.
  - Full matrix: 1,200 1.2/1200 (1000×), 12,345 12.345/12345, 1,234,567 NaN→dropna (row dropped) vs 1234567, -1,200 -1.2/-1200, 1,200.00 correct both (both-separators branch), 1.200 correct.
  - Docs ingestion/import-pipeline.md:39-46 item 3 documents buggy single-comma as decimal; §4.5 correctly claims both guard spec but bank code diverges — docs accurate description of bug.
  - R4 commit dab118a fixed dot-thousands per-value but left comma guard — pre-existing.
- **Impact:** 1000× understatement or silent drop; likelihood low for EU-primary Revolut/N26/Wise exports but affects generic US CSV; review-first editor mitigation (user sees 1.2 in data_editor before persist).
- **Fix hint (no edit done):** Mirror PDF guard in bank conv after _pure_dot_thousands: elif "," in s: groups=s.split(","); if all(len(g)==3 for g in groups[1:] and g0 digits): s="".join(groups) else: replace.

### LEAD-004 — tests/conftest temp DB permission
- **Location:** tests/conftest.py:22-27, db.py:36-42, crypto.py:54-61, tests/test_github_backup.py:36-38
- **Observation:** DB-backed fixtures fail with sqlcipher unable to open file when temp dir restricted — infra masks signal
- **Re-validated:** **NOT-A-BUG (infra fixed for claimed scope)** — confidence HIGH — validator e8a84a53, 18/18 pass live
- **Trace:** workspace-write sandbox denies system temp (/Temp/dsh-*, pytest-cache-files-*) → permission denied warnings (120+ git stderr). Fix: conftest now uses data/_pytest_tmp (workspace-allowed) with os.environ forced DB_PATH/BACKUP_DIR, colocated _DB_DIR/_ENCRYPTION_LOCK/BACKUP_DIR/.secret_key (T2-001 P4). Probe shows DB_PATH under data/_pytest_tmp/probe_* init_db OK sqlcipher SELECT OK. tests/test_db + test_crypto 18 PASS. Residual: pytest cache still warns WinError 5 (harmless) + tests/test_github_backup _tmpfile still uses tempfile.mkdtemp system temp → PermissionError (1 failed) — out of LEAD scope but same class, should migrate to data/_pytest_tmp in future hardening. Dead import tempfile retained.

---

## 4. Updated Backlog & Recommended Next Steps

- **Immediately fixable (PROMOTED):**
  - LEAD-001 isolated _eur CREATE guard — 4 tables. Single patch in _recompute_derived_eur + validate_fields (reject or recompute). Test with probe_LEAD-001 matrix (900k accepted/5M rejected/UPDATE recomputed).
  - LEAD-003 bank comma-thousands guard — one branch in _to_numeric_locale mirroring pdf_import 171-178. Covered by existing T6/ingestion tests + reproduction matrix.
- **Docs-only:**
  - T7-005 ask.py:21 caption wording — 1-line copy fix.
- **Defer / hardening TODO (INFO):**
  - T1-001 throttle bound (only if threat model expands beyond home-LAN) + T1-003 empty-string UX normalization + T2-002 RETURNING determinism (single line: SELECT max or caller-only RETURNING) + T8-005 persistence if API ever public/multi-worker + LEAD-004 migrate github_backup temp.

---

## 5. Validator Provenance

- Validators: validator-T1-001-throttle-leak (715d001d) ready, validator-T1-003-allow-reg (d255d5a7) ready, validator-T2-002-household-bump (f76db170) ready (CONFIRMED-LOW, healed next rerun), validator-T3-003-taxonomy-remap (46c47b18) ready, validator-T7-005-ask-doc-overclaim (811bbed7) ready, validator-T8-005-pairing-ratelimit (85334f0b) ready, validator-LEAD-001-isolated-eur (be5b80a3) ready, validator-LEAD-002-hex-ux (6d41ad91) ready, validator-LEAD-003-us-thousands (6c3393c5) ready, validator-LEAD-004-temp-perm (e8a84a53) ready.
- Artifacts: qa/_tmp/probe_* (ephemeral), live DB probes with DB_PATH override.

---

## 6. Risks Still Open From Prior Remediation

- Isolated _eur bypass (LEAD-001) is sibling of P1 systemic — already-capped but bypasses FX poisons aggregates up to 1M per record via paired device.
- US thousands (LEAD-003) under-states 1000× per affected row — mitigated by review-first editor.
- Household RETURNING arbitrary (T2-002) causes single-rerun staleness only — not persistent.