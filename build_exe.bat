@echo off
rem ── Build ExpenseTracker.exe — a desktop launcher for the app ──
rem Requires Python with PyInstaller (installed automatically if missing).
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Expense Tracker - build desktop launcher
echo  ============================================================
echo.

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller. Check your Python installation.
        pause
        exit /b 1
    )
)

python -m PyInstaller --onefile --noconsole --name ExpenseTracker ^
    --distpath dist --workpath build --specpath build ^
    launcher.py
if errorlevel 1 (
    echo Build failed - see the output above.
    pause
    exit /b 1
)

copy /Y "dist\ExpenseTracker.exe" "ExpenseTracker.exe" >nul
echo.
echo Done! Double-click ExpenseTracker.exe to start the app.
echo (You can copy that single file to your Desktop - it finds the project
echo  folder next to it, so keep the project folder intact.)
pause
