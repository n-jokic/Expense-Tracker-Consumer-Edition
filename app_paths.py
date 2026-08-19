"""Read-only bundled resources and per-user writable application state."""

import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def state_dir() -> Path:
    configured = os.environ.get("EXPENSE_TRACKER_DATA_DIR")
    if configured:
        return Path(configured)
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ExpenseTracker"
    return resource_dir() / "data"


def model_dir() -> Path:
    return state_dir() / "models"
