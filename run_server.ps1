# ── Expense Tracker — run the server and make it reachable from your phone ──
# Usage:  .\run_server.ps1   (or right-click → "Run with PowerShell")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host " ============================================================"
Write-Host "   Expense Tracker"
Write-Host " ============================================================"
Write-Host "   The app opens at http://localhost:8501"
Write-Host "   On your phone (same Wi-Fi), scan the QR code in the"
Write-Host "   sidebar or open the Network URL shown when the app starts."
Write-Host ""
Write-Host "   FIRST RUN: if Windows Firewall asks, allow access on"
Write-Host "   Private networks."
Write-Host " ============================================================"
Write-Host ""

# Activate the virtual environment if it exists
if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

# Install dependencies if Streamlit is missing
python -c "import streamlit" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies..."
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to install dependencies. Check your Python installation." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# Optionally start the phone sync API (port 8502) in a separate window
Start-Process python -ArgumentList "api.py" -WindowStyle Minimized

streamlit run app.py --server.address 0.0.0.0
