"""#27 — UI consistency kit: canonical KPI band adoption, palette
discipline (no raw hex outside ui/styles.py, charts on CHART_COLORS)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_page_kpi_band_helper_exists_and_is_canonical():
    src = _src("ui", "panel.py")
    assert "def page_kpi_band(metrics" in src
    assert "border=True" in src          # bordered cards are part of the kit


def test_migrated_pages_use_the_kpi_band_helper():
    for page in ("loans.py", "travel.py", "savings.py"):
        assert "page_kpi_band([" in _src("app_pages", page), page


HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")


def test_no_raw_hex_outside_ui_styles():
    offenders = []
    for directory in ("app_pages", "ui"):
        for py in ROOT.joinpath(directory).glob("*.py"):
            if py.name == "styles.py":
                continue
            for i, line in enumerate(
                    py.read_text(encoding="utf-8").splitlines(), 1):
                if HEX_RE.search(line):
                    offenders.append(f"{py.name}:{i}")
    assert offenders == []


def test_inline_charts_use_the_palette():
    # every plotly express call in app_pages sits within 4 lines of a
    # palette reference (CHART_COLORS / C_* constants).
    bad = []
    for py in ROOT.joinpath("app_pages").glob("*.py"):
        lines = py.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if re.search(r"px\.(bar|line|pie|area)\(", line):
                window = "\n".join(lines[i:i + 5])
                if not re.search(r"CHART_COLORS|C_[A-Z]+", window):
                    bad.append(f"{py.name}:{i + 1}")
    assert bad == []
