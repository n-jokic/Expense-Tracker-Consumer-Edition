# FIN-00 Pre-Change Baseline

Recorded: 2026-08-21 17:55–18:05 (+02:00)
Scope: environment + measurement only. No application code, tests, or requirements files were modified.

## Git state at recording time

- Branch: `main`
- HEAD: `13b9a923378c675e9594abbaa83739edea4db64e` (`13b9a92`)
- `git status --short` (before baseline files were written):
  ```
   M tasks/plan.md
  ?? .venv-fin00/
  ?? qa/baseline-pip-freeze.txt
  ```
  (`tasks/plan.md` modification predates this task — orchestrator edit; left untouched.)
- `git status --short` after the test runs:
  ```
   M tasks/plan.md
  ?? qa/baseline-pip-freeze.txt
  ?? qa/baseline/
  ```

## Interpreter

- `py -0p`:
  ```
   -V:3.12 *        C:\Users\Nikita\AppData\Local\Programs\Python\Python312\python.exe
  ```
- Venv `python -VV`: `3.12.10 (tags/v3.12.10:0cc8128, Apr  8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]`

## Venv creation method that worked

1. Plain `py -3.12 -m venv .venv-fin00` → **failed twice** with ensurepip exit status 1 (reproduced; base-level `py -3.12 -m ensurepip --version` reports pip 25.0.1 fine).
2. Fallback `uv venv` (uv 0.12.3, `C:\Users\Nikita\.local\bin\uv.exe`) → **worked**, with two sandbox-required adjustments:
   - `UV_CACHE_DIR` pointed into the workspace (default `%LOCALAPPDATA%\uv\cache` is outside the session sandbox → Access denied).
   - For the one sdist build (`antlr4-python3-runtime==4.9.3`, pulled by `rapidocr→omegaconf`; no wheel on PyPI), `TMP`/`TEMP` had to point at a plain workspace dir, otherwise setuptools' build-isolation temp under uv's `builds-v0` gets Permission denied.
   - venv seeded with pip 26.2.1 (`uv venv --seed`).
3. Packages installed from canonical sources: `-r requirements.txt -r requirements-dev.txt` (dev file itself includes `-r requirements.txt`). Final sync verified by uv ("Checked 48 packages", exit 0).

## Import probe (all via `.venv-fin00\Scripts\python.exe`)

| module | result |
|---|---|
| streamlit | OK **1.61.1** (matches pin) |
| sqlalchemy | OK 2.0.52 |
| pandas | OK 3.0.5 |
| plotly | OK 6.9.0 |
| sklearn | OK 1.9.0 |
| statsmodels | OK 0.14.6 |
| pytesseract | OK 0.3.13 |
| rapidocr | OK (no `__version__`) |
| onnxruntime | OK 1.29.0 |
| cv2 | OK 5.0.0 |
| fitz (PyMuPDF) | OK 1.28.2 (deprecation notice: use `import pymupdf`) |
| pdfplumber | OK 0.11.10 |
| fastapi | OK 0.141.1 |
| sqlcipher3 | OK (no `__version__`) |
| mcp | OK (no `__version__`) |
| requests | OK 2.34.2 |
| bcrypt | OK 5.0.0 |
| openpyxl | OK 3.1.5 |
| yaml | OK 6.0.3 |
| cryptography | OK 50.0.0 |
| qrcode | OK (no `__version__`) |
| PIL | OK 12.3.0 |
| pytest | OK 9.1.1 |
| httpx | OK 0.28.1 |

24/24 modules import successfully.

## Freeze

`.venv-fin00\Scripts\python.exe -m pip freeze` → 94 packages → `qa/baseline-pip-freeze.txt`.

## Test suite results

### Canonical command from repo root: `.venv-fin00\Scripts\python.exe -m pytest -q`

**Interrupted: 119 errors during collection — 2 warnings, 119 errors in 3.78s (exit 2). No tests ran.**

Every error is `PermissionError: [WinError 5] Access is denied` while pytest scans stale temp directories left inside the workspace by previous (foreign-session) pytest runs: `data\_pytest_tmp\expense_tracker_tests_*\...`, `data\pytest-cache-files-*`, `data\bt_ask`, `data\bt_combined`, `data\expense_tracker_tests__r4bnr72`. There is no root `pytest.ini`/`pyproject.toml`/`setup.cfg` (no `testpaths`), so bare collection recurses through `data/`. Those stale dirs are not deletable from this sandboxed session (Remove-Item denied on all 20 targets; ACLs show foreign principals e.g. `CodexSandboxUsers`, orphaned SIDs). Dot-directories (`.pytest_cache`, `tests/.pytest_cache`) are skipped by pytest's default `norecursedirs` and are harmless to collection.

### Full suite scoped to its actual location: `.venv-fin00\Scripts\python.exe -m pytest -q tests`

**7 failed, 535 passed, 10 errors, 32 warnings in 47.96s (exit 1).**

## Classified failure list

### (a) Environment/setup failures — 16 (deterministic; identical set reproduced on re-run)

All are `PermissionError` raised by the DSH file sandbox against temp dirs/files created *during the run* under `data\_pytest_tmp\<run-uuid>\pytest_runtime\`. Same failures reproduce bit-for-bit on a second run of exactly these 16 tests (6 failed, 26 passed, 10 errors). Not application logic.

FAILED (4) — `tests/test_github_backup.py` (helper `_tmpfile()` → `tempfile.mkdtemp(prefix="gh_backup_tests_")` then `open(path,"wb")` → PermissionError Errno 13):
- `tests/test_github_backup.py::test_split_merge_roundtrip_single_and_multi_part` — cannot create `gh_backup_tests_*/one.db`
- `tests/test_github_backup.py::test_merge_rejects_tampered_and_missing_parts` — cannot create `gh_backup_tests_*/t.db`
- `tests/test_github_backup.py::test_find_manifest_and_download_roundtrip` — cannot create `gh_backup_tests_*/x.db`
- `tests/test_github_backup.py::test_replace_db_wraps_file_in_use_error` — cannot create `gh_backup_tests_*/restored.db`

FAILED (2) — `tests/test_recurring.py` (`AppTest.from_string` writes temp script under TMPDIR → PermissionError Errno 13):
- `tests/test_recurring.py::test_persist_not_invoked_when_action_present`
- `tests/test_recurring.py::test_persist_invoked_when_no_action`

ERROR at setup (10) — pytest `tmp_path` factory basetemp `...\pytest_runtime\pytest-of-Nikita` is Access-denied for scandir (WinError 5):
- `tests/test_llm.py::test_local_provider_discovers_app_model`
- `tests/test_llm.py::test_local_model_preserves_zero_gpu_layers_and_reports_missing_file`
- `tests/test_llm.py::test_local_model_retries_cpu_after_vulkan_load_failure`
- `tests/test_llm.py::test_local_import_oserror_is_caught`
- `tests/test_llm.py::test_local_import_importerror_message_source_vs_frozen`
- `tests/test_llm.py::test_stale_diagnostic_cleared_on_successful_reload`
- `tests/test_llm.py::test_local_cache_key_includes_gpu_layers`
- `tests/test_llm.py::test_local_runtime_status`
- `tests/test_launcher.py::test_first_launch_migrates_legacy_state_once`
- `tests/test_app_ui.py::test_ask_page_error_does_not_pollute_history`

Related non-fatal noise (same root cause): 2× PytestCacheWarning (cannot write `.pytest_cache\v\cache`); interpreter-exit `weakref._cleanup` traceback failing to rmtree the run's temp dir.

### (b) Pre-existing application/test failures — 1

- `tests/test_ocr_review.py::test_receipt_review_shows_uncertainty_and_reuses_result` — app raises `StreamlitAPIException: Forms cannot be nested in other forms.` at `app.py:120` (`with st.form("rate_form")` nested inside an existing form when the log-expense page renders); test asserts `not at.exception`.

### (c) Anything else — none

No timeouts, no crashes beyond the sandbox artifacts listed above.

## Notes

- `requirements-ocr-advanced.txt` was read and **skipped**: its header declares it an optional server-only document parsing stack ("Keep this out of the normal desktop/runtime installation" — paddlepaddle/paddlex/paddleocr). Not installed.
- `requirements-ai.txt` not installed per brief (optional llama.cpp runtime).
- Hermeticity confirmed: `tests/conftest.py` forces `DB_PATH`/`BACKUP_DIR`/`TEMP/TMP/TMPDIR` into `data/_pytest_tmp/<uuid>` with a fixed test-only SQLCipher key; live `data/expense_tracker.db` untouched.
- The bare-command collection blockage and the 16 sandbox PermissionErrors share one environmental root cause: the DSH workspace-write sandbox denies access to filesystem entries created by other sessions (and intermittently to fresh subdirs created mid-run). Cleaning `data/_pytest_tmp/*`, `data/pytest-cache-files-*`, `.pytest_cache`, `tests/.pytest_cache` from an unsandboxed/elevated shell should make the canonical bare `pytest -q` collect normally and likely turn most/all of the 16 env failures green.
- Baseline verdict for FIN-00: environment is trustworthy (Python 3.12.10, streamlit 1.61.1, all imports OK, deps installed from canonical requirements); measured pre-change state = **535 passed / 7 failed / 10 errors**, of which only **1 failure is a genuine pre-existing application bug** (nested forms, `app.py:120`).
