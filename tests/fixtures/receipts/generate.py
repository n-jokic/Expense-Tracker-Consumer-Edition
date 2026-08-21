"""
Generate the REAL OCR receipt fixtures (OCR-01).

Renders each manifest entry as an actual raster image an OCR engine must
read — no mocked pixels. Run from the repo root:

    .venv-fin00\\Scripts\\python.exe tests\\fixtures\\receipts\\generate.py

The generated images are committed alongside this script so tests never
depend on regeneration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

HERE = Path(__file__).resolve().parent
W, H = 620, 980


def _font(size: int, bold: bool = False):
    # Arial first: its plain zero OCRs far better than Consolas' slashed one.
    for name in ("arialbd.ttf" if bold else "arial.ttf",
                 "consolab.ttf" if bold else "consola.ttf"):
        path = Path(r"C:\Windows\Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _amt(value: float, style: str) -> str:
    """Format an amount in the locale style of the receipt."""
    if style == "rsd":
        s = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return s
    return f"{value:.2f}"


def render(case: dict) -> Image.Image:
    lang = case["language"]
    style = "rsd" if case["currency"] == "RSD" else "eur"
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)
    f_big, f_mid, f_small = _font(44, True), _font(36), _font(32)

    y = 30
    def line(text="", font=f_mid, gap=None):
        nonlocal y
        if text:
            d.text((40, y), text, fill="#111111", font=font)
        y += gap or (font.size + 12)

    line(case["merchant"], f_big)
    line("Beograd 11000, Trg 1" if lang.startswith("sr")
         else "London Rd 12, Springfield")
    line(case["date"] + "  14:32", f_small)
    line("-" * 34)

    items = case.get("items", [])
    for it in items:
        qty = it.get("quantity", 1)
        unit = it.get("unit_price", it["line_total"])
        if qty != 1:
            left = f"{it['description']} {qty} x {_amt(unit, style)}"
        else:
            left = it["description"] if len(it["description"]) <= 26 \
                else it["description"][:25] + "."
        total_txt = _amt(it["line_total"], style)
        if it["line_total"] < 0 or unit < 0:
            total_txt = "-" + total_txt.lstrip("-")
            left = left.replace(f"-{_amt(unit, style)}", _amt(unit, style))
        d.text((40, y), left, fill="#111111", font=f_mid)
        d.text((W - 40 - d.textlength(total_txt, font=f_mid), y),
               total_txt, fill="#111111", font=f_mid)
        y += f_mid.size + 12

    line("-" * 34)
    feats = case.get("features", [])
    items_sum = round(sum(i["line_total"] for i in items), 2)
    if "subtotal" in feats:
        line(("Medjuzbir" if lang.startswith("sr") else "Subtotal")
             + "   " + _amt(items_sum, style))
    if "vat" in feats:
        line("PDV je u ceni" if lang.startswith("sr") else "VAT included", f_small)
    line("=" * 34)
    d.text((40, y), ("UKUPNO" if lang.startswith("sr") else "TOTAL"),
           fill="#000000", font=_font(46, True))
    tot = _amt(case["total"], style)
    d.text((W - 40 - d.textlength(tot, font=_font(46, True)), y),
           tot, fill="#000000", font=_font(46, True))
    y += 66
    if "cash" in feats:
        line(("Gotovina   " if lang.startswith("sr") else "Cash   ")
             + _amt(max(case["total"], 5000.0 if style == "rsd" else 20.0), style))
    if "change" in feats:
        change = (max(case["total"], 5000.0 if style == "rsd" else 20.0)
                  - case["total"])
        line(("Kusur     " if lang.startswith("sr") else "Change   ")
             + _amt(round(change, 2), style))
    line("-" * 34)
    line("HVALA NA POSETI!" if lang.startswith("sr") else "THANK YOU!", f_small)

    cropped = img.crop((0, 0, W, min(H, y + 30)))
    quality = case.get("quality")
    geometry = case.get("geometry")
    if geometry in ("warped", "angled"):
        cropped = cropped.rotate(7, expand=True, fillcolor="#ffffff")
    elif geometry == "rotated":
        cropped = cropped.rotate(-5, expand=True, fillcolor="#ffffff")
    if quality in ("dark",):
        cropped = ImageEnhance.Brightness(cropped).enhance(0.55)
    elif quality == "overexposed":
        bg = Image.new("RGB", (cropped.width + 60, cropped.height + 60), "#f2f2f2")
        bg.paste(cropped, (30, 30))
        cropped = bg
    return cropped


def main() -> int:
    manifest_path = HERE / "manifest.json"
    cases = json.loads(manifest_path.read_text(encoding="utf-8"))
    made = []
    for case in cases:
        out = HERE / case["image"]
        img = render(case)
        save_kw = {"quality": 92} if out.suffix.lower() == ".jpg" else {}
        img.save(out, **save_kw)
        made.append(out.name)
    print("generated:", ", ".join(made))
    return 0


if __name__ == "__main__":
    sys.exit(main())
