"""
Shared test configuration.

Isolates the SQLite database: pytest imports this module before any test
module, so setting DB_PATH/BACKUP_DIR here guarantees the whole suite runs
against a throwaway database instead of data/expense_tracker.db.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="expense_tracker_tests_")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "test_expense_tracker.db"))
os.environ.setdefault("BACKUP_DIR", os.path.join(_TMP, "backups"))
