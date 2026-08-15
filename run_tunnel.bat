@echo off
rem ── Expense Tracker — expose the local app over a public Cloudflare tunnel ──
rem Requires: cloudflared (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
rem NOTE: the URL is PUBLIC — before exposing, set ALLOW_REGISTRATION=false
rem       (environment variable or .streamlit/secrets.toml).

where cloudflared >nul 2>nul
if errorlevel 1 (
    echo cloudflared not found. Download it from:
    echo   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   Public URL appears below (trycloudflare.com) — open it on
echo   your phone from ANY network, not just your home Wi-Fi.
echo.
echo   SECURITY: anyone with the URL reaches the login page.
echo   Disable registration first: set ALLOW_REGISTRATION=false
echo  ============================================================
echo.

cloudflared tunnel --url http://localhost:8501

pause
