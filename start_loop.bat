@echo off
cd /d "%~dp0"
title Sky Loop v7.1
echo ========================================
echo   Sky Loop v7.1
echo   Chat engine: sky-companion port
echo   Input backend: Arduino (auto port)
echo ========================================
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-flash
set SKY_VISION_ENABLED=1
set SKY_VISION_PROVIDER=deepseek
set SKY_INPUT_BACKEND=arduino
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=çæçæ,çÛçÛ,°¢ÑÕ

echo Current config:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   Input backend: %SKY_INPUT_BACKEND%
echo   Web search: %SKY_SEARCH_ENABLED%
echo   Whitelist: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
echo   Player: çæçæ  AI: ÐÇºÓ
echo.

if not exist key.txt (
    echo [WARNING] key.txt not found
    echo Please create key.txt with your API key
    echo.
)

echo [1/2] Checking python...
py --version
echo.
echo [2/2] Starting main loop...
py sky-loop-v7.py 2>&1
echo.
echo [Loop exited with code %ERRORLEVEL%]
pause


