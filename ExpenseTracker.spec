# -*- mode: python ; coding: utf-8 -*-
from glob import glob
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

datas, binaries, hiddenimports = [], [], []
for package in ("streamlit", "llama_cpp", "sqlcipher3", "pandas", "plotly",
                "bcrypt", "openpyxl", "yaml", "cryptography", "qrcode", "PIL",
                "sklearn", "statsmodels", "pytesseract", "pdfplumber", "fastapi",
                "uvicorn", "mcp", "requests"):
    collected_datas, collected_binaries, collected_hiddenimports = collect_all(package)
    datas += collected_datas
    binaries += collected_binaries + collect_dynamic_libs(package)
    hiddenimports += collected_hiddenimports

datas += [(source, ".") for source in glob("*.py")]
datas += [("app_pages", "app_pages"), (".streamlit", ".streamlit"), ("README.md", ".")]

a = Analysis(["launcher.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ExpenseTracker", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ExpenseTracker")
