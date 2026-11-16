@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Sky With You - Setup
echo ========================================
echo   Sky With You / One-stop Setup
echo ========================================
echo.
py setup.py
echo.
pause