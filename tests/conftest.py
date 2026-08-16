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
