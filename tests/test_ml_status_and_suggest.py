"""#23 — manual-entry suggestion picker + ML status line (pure logic)."""
import pandas as pd

import forecasting
from forecasting import ml_status_line, pick_manual_suggestion


def _df(rows):
    return pd.DataFrame(rows, columns=["description", "category", "subcategory"])


# ── pick_manual_suggestion ──────────────────────────────────────────────────

def test_pick_empty_description_returns_none(monkeypatch):
    monkeypatch.setattr(
        forecasting, "suggest_category_and_subcategory",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert pick_manual_suggestion(_df([]), "   ") is None
    assert pick_manual_suggestion(None, "") is None


def test_pick_no_category_returns_none(monkeypatch):
    monkeypatch.setattr(
        forecasting, "suggest_category_and_subcategory",
        lambda df, text, user_id=None: (None, "", 0.0, 0.0))
    assert pick_manual_suggestion(_df([]), "mystery item") is None


def test_pick_keyword_source_when_conf_zero(monkeypatch):
    monkeypatch.setattr(
        forecasting, "suggest_category_and_subcategory",
        lambda df, text, user_id=None: ("Groceries", "", 0.0, 0.0))
    out = pick_manual_suggestion(_df([]), "lidl run")
    assert out == {"cat": "Groceries", "sub": "", "conf": 0.0,
                   "sub_conf": 0.0, "source": "keywords"}


def test_pick_classifier_source_and_sub_cleanup(monkeypatch):
    monkeypatch.setattr(
        forecasting, "suggest_category_and_subcategory",
        lambda df, text, user_id=None: ("Groceries", "—", 0.87, 0.0))
    out = pick_manual_suggestion(_df([]), "lidl run", user_id=7)
    assert out["source"] == "classifier"
    assert out["sub"] == ""          # placeholder dash cleaned up
    assert out["conf"] == 0.87


# ── ml_status_line ──────────────────────────────────────────────────────────

def test_status_active_model():
    line = ml_status_line(3, 0)
    assert "v3" in line and "active" in line.lower()


def test_status_keyword_missing_counts():
    line = ml_status_line(None, 4)
    assert "6 more labelled expense" in line


def test_status_keyword_singular():
    assert "1 more labelled expense " in ml_status_line(None, 9) + " "


def test_status_keyword_ready_to_train():
    line = ml_status_line(None, 25)
    assert "candidate" in line and "activate" in line.lower()


def test_status_never_negative():
    assert "more" not in ml_status_line(None, 99)
