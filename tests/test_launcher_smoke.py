"""
PKG-01 regression tests — packaged-build sanity gate and bundle layout.

The exe itself cannot be executed on every dev host (Windows Application
Control blocks freshly built unsigned binaries), so these tests verify what
the smoke check guarantees by calling its logic directly, and audit the
built bundle's contents when a build exists.
"""

import importlib.util
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location(
        "launcher_under_test", os.path.join(REPO, "launcher.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_smoke_passes_on_source_tree(launcher, monkeypatch):
    """streamlit + sqlcipher3 importable and app.py present -> OK even when
    the optional llama_cpp runtime is NOT installed (PKG-01: the old check
    hard-required llama_cpp and failed every standard build)."""
    monkeypatch.setattr(launcher, "_project_dir", lambda: REPO)
    launcher._smoke_check()          # must not raise


def test_smoke_requires_packaged_app_py(launcher, monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_project_dir", lambda: str(tmp_path))
    with pytest.raises(RuntimeError, match="app.py"):
        launcher._smoke_check()


def test_bundled_first_party_packages_present():
    """When a PyInstaller build exists, every first-party package must be
    shipped as loose files next to app.py (pages are executed from disk, so
    their imports are invisible to PyInstaller analysis)."""
    internal = os.path.join(REPO, "dist", "ExpenseTracker", "_internal")
    if not os.path.isdir(internal):
        pytest.skip("no PyInstaller build present (run ExpenseTracker.spec)")
    for entry in ("app.py", "app_pages", ".streamlit",
                  "services", "ai", "ingestion", "ml",
                  "domain", "ui", "infra"):
        assert os.path.exists(os.path.join(internal, entry)), \
            f"packaged bundle is missing {entry}"
    # spot-check modules landed by this contract
    for rel in ("domain/validation.py", "services/purchase_commands.py",
                "ai/charts.py", "ingestion/receipt/line_item_extractor.py"):
        assert os.path.isfile(os.path.join(internal, *rel.split("/")))
