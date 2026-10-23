@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator - please click YES.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
echo ========================================
echo   Capture backend diagnostic (15s)
echo   Switch to game, then LOOK/MOVE around
echo ========================================
py diag_capture.py
echo.
pause
