@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (Vision)
echo ========================================
echo   Sky Loop v7.1 + VLM Pose Check
echo   Input backend: Arduino (auto port)
echo   Vision: qwen-vl-plus (SKY_VISION_ENABLED=1)
echo ========================================
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-v4-flash
set SKY_INPUT_BACKEND=arduino
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=çæçæ,çÛçÛ,°¢ÑÕ
set SKY_VISION_ENABLED=1

echo Current config:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   Input backend: %SKY_INPUT_BACKEND%
echo   Web search: %SKY_SEARCH_ENABLED%
echo   Whitelist: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
echo   Vision VLM: ENABLED (qwen-vl-plus pose check)
echo   Player: çæçæ  AI: ÐÇºÓ
echo.

if not exist key.txt (
    echo [WARNING] key.txt not found
    echo.
)
if not exist vision_key.txt (
    echo [WARNING] vision_key.txt not found, VLM will stay disabled
    echo.
)

echo [1/2] Checking python...
py --version
echo.
echo [2/2] Starting main loop (with vision)...
py sky-loop-v7.py 2>&1
echo.
echo [Loop exited with code %ERRORLEVEL%]
pause
