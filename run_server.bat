@echo off
rem ── Expense Tracker — run the server and make it reachable from your phone ──
rem Plain HTTP by default (local/LAN use). To enable HTTPS with a self-signed
rem certificate instead, set:  set EXPENSE_TRACKER_TLS=1
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Expense Tracker
echo  ============================================================
echo    The app opens at http://localhost:8501
echo    On your phone (same Wi-Fi), scan the QR code in the
echo    sidebar or open the Network URL shown when the app starts.
echo    (Optional) For HTTPS with a self-signed certificate, set
echo    EXPENSE_TRACKER_TLS=1 before running this script.
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

rem TLS is OPT-IN: set EXPENSE_TRACKER_TLS=1 to serve HTTPS with a self-signed
rem certificate (the sync API below reads the same variable).
set USE_TLS=0
if "%EXPENSE_TRACKER_TLS%"=="1" set USE_TLS=1
if "%USE_TLS%"=="1" (
    rem No-op when data\certs\cert.pem and key.pem already exist.
    python make_cert.py
    if errorlevel 1 (
        echo Failed to create the TLS certificate. Install cryptography and retry.
        pause
        exit /b 1
    )
)

rem Optionally start the phone sync API (port 8502)
start "ExpenseTracker Sync API" /min python api.py

if "%USE_TLS%"=="1" (
    streamlit run app.py --server.address 0.0.0.0 --server.sslCertFile data\certs\cert.pem --server.sslKeyFile data\certs\key.pem --server.headless true
) else (
    streamlit run app.py --server.address 0.0.0.0 --server.headless true
)

pause
