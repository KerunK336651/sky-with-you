@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Sky With You - Song Setup
echo ========================================
echo   Sky With You / Song Setup
echo   Set quick-slot key (1-8) and instrument
echo ========================================
echo.
py setup_song.py
echo.
pause
