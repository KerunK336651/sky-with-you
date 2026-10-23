@echo off
cd /d "%~dp0"
title Sky YOLO Preview (view only)
echo ========================================
echo   Sky YOLO Preview - view only, no keys
echo   Captures the game window and shows
echo   live YOLO detections. Press q to quit.
echo ========================================
echo.
py yolo_preview.py
echo.
echo [Preview exited with code %ERRORLEVEL%]
pause
