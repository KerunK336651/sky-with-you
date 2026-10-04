@echo off
cd /d "%~dp0"
title Sky Debug Web Launcher

rem ====== Request admin (Arduino backend needs it) ======
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ========================================
    echo   Sky With You - Web Debug Launcher
    echo ========================================
    echo Requesting administrator privileges, please accept the UAC prompt...
    timeout /t 2 /nobreak >nul
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [1/3] Starting MCP server...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"

echo [2/3] Waiting 3 seconds for MCP to come up...
timeout /t 3 /nobreak >nul

echo [3/3] Starting Web Debug server...
start "Sky Debug Web" cmd /k "cd /d %~dp0 && py debug_web.py"

echo Waiting for debug server to load...
timeout /t 5 /nobreak >nul

echo Opening browser...
start "" "http://127.0.0.1:9911"
exit /b
