@echo off
chcp 65001 >nul
rem Auto-elevate to admin (needed to capture keys while game runs as admin)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges - please click YES in the UAC dialog.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
echo ========================================
echo   Sky Interaction Collector (GUI)
echo   Numpad 1-9 select within page, 0 negative
echo   F6 flip page  F7/F8 prev/next type
echo   F9 shot  F10 burst  F12 quit
echo ========================================
py collect_gui.py
echo.
pause
