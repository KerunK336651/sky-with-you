@echo off
cd /d "%~dp0"
title Sky MCP Server (Arduino)
echo ========================================
echo   Sky MCP Server
echo   Input backend: Arduino (auto-detect port)
echo   Port: 9900
echo ========================================
echo.
echo Using Arduino Pro Micro (ATmega32U4) hardware keyboard.
echo More stable than software simulation, won't be blocked by games.
echo.
echo [1/2] Checking python...
py --version
echo.
echo [2/2] Starting MCP server...
set SKY_SKIP_FOCUS=1
rem Auto-detect Arduino by USB ID; uncomment to force a port:
rem set SKY_SERIAL_PORT=COM4
py sky-mcp-server.py --http --port 9900 --token 1234 --input-backend arduino 2>&1
echo.
echo [MCP server exited with code %ERRORLEVEL%]
pause


