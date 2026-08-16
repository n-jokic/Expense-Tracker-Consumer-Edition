"""
Shared test configuration.

Isolates the SQLite database: pytest imports this module before any test
module, so setting DB_PATH/BACKUP_DIR here guarantees the whole suite runs
against a throwaway database instead of data/expense_tracker.db.

The assignment is FORCED (not setdefault): an ambient DB_PATH in the shell
would otherwise point the suite — including tests that DROP and recreate
tables — at the live database.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="expense_tracker_tests_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_expense_tracker.db")
os.environ["BACKUP_DIR"] = os.path.join(_TMP, "backups")
# The suite exercises the real encryption path (SQLCipher). A fixed key keeps
# tests hermetic: they never read or generate the live data/.secret_key, and
# the test DB can always be opened by the same key.
os.environ["EXPENSE_TRACKER_DB_KEY"] = "9f2c8e6a1b4d7f3e5c8a2b6d4f1e7c3a5b8d2f6e1a4c7b3d5f8e2a6c1b4d7f3"
