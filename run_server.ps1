# ── Expense Tracker — run the server and make it reachable from your phone ──
# Usage:  .\run_server.ps1   (or right-click → "Run with PowerShell")
# Plain HTTP by default (local/LAN use). To enable HTTPS with a self-signed
# certificate instead, set:  $env:EXPENSE_TRACKER_TLS = "1"

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host " ============================================================"
Write-Host "   Expense Tracker"
Write-Host " ============================================================"
Write-Host "   The app opens at http://localhost:8501"
Write-Host "   On your phone (same Wi-Fi), scan the QR code in the"
Write-Host "   sidebar or open the Network URL shown when the app starts."
Write-Host "   (Optional) For HTTPS with a self-signed certificate, set"
Write-Host "   EXPENSE_TRACKER_TLS=1 before running this script."
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

# TLS is OPT-IN: set EXPENSE_TRACKER_TLS=1 to serve HTTPS with a self-signed
# certificate (the sync API below reads the same variable).
$useTls = ($env:EXPENSE_TRACKER_TLS -eq "1")

if ($useTls) {
    # No-op when data\certs\cert.pem and key.pem already exist.
    python make_cert.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create the TLS certificate. Install cryptography and retry." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# Optionally start the phone sync API (port 8502) in a separate window
Start-Process python -ArgumentList "api.py" -WindowStyle Minimized

$streamlitArgs = @(
    "run", "app.py",
    "--server.address", "0.0.0.0",
    "--server.headless", "true"
)
if ($useTls) {
    $streamlitArgs += "--server.sslCertFile", "data\certs\cert.pem"
    $streamlitArgs += "--server.sslKeyFile", "data\certs\key.pem"
}
& streamlit @streamlitArgs
