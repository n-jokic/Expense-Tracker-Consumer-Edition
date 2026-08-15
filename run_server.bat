@echo off
rem ── Expense Tracker — run the server and make it reachable from your phone ──
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Expense Tracker
echo  ============================================================
echo    The app opens at http://localhost:8501
echo    On your phone (same Wi-Fi), scan the QR code in the
echo    sidebar or open the Network URL shown when the app starts.
echo.
echo    FIRST RUN: if Windows Firewall asks, allow access on
echo    Private networks.
echo  ============================================================
echo.

rem Activate the virtual environment if it exists
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

rem Install dependencies if Streamlit is missing
python -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies. Check your Python installation.
        pause
        exit /b 1
    )
)

streamlit run app.py --server.address 0.0.0.0

pause
