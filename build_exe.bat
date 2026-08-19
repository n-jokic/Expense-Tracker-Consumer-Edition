@echo off
rem ── Build the self-contained Windows x64 installer ──
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Expense Tracker - build desktop launcher
echo  ============================================================
echo.

python -m pip install --upgrade pip pyinstaller==6.16.0
if errorlevel 1 goto :fail
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail
python -m PyInstaller --noconfirm --clean ExpenseTracker.spec
if errorlevel 1 (
    echo Build failed - see the output above.
    goto :fail
)
dist\ExpenseTracker\ExpenseTracker.exe --smoke
if errorlevel 1 goto :fail
iscc installer.iss
if errorlevel 1 goto :fail
echo.
echo Done! dist\installer\ExpenseTracker-Setup.exe is ready.
pause
exit /b 0

:fail
echo Build failed. Install Inno Setup 6 and check the output above.
pause
exit /b 1
