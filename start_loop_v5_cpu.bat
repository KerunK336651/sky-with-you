@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (PP-OCRv5 / OCR on CPU)
echo ========================================
echo   Sky Loop v7.1  --  OCR FORCED TO CPU
echo   Chat engine: sky-companion port
echo   Input backend: Arduino (auto port)
echo ========================================
echo.
echo   This variant is ONLY for the frame-drop A/B test:
echo   compare in-game FPS against start_loop_v5.bat (OCR on DML/GPU).
echo   If FPS is the same, go back to start_loop_v5.bat (it is ~1.4x faster).
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-flash
set SKY_VISION_ENABLED=1
set SKY_VISION_PROVIDER=deepseek
set SKY_INPUT_BACKEND=arduino
set SKY_OCR_MODEL=v5
set SKY_OCR_DEVICE=cpu
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=çæçæ,çÛçÛ,°¢ÑÕ

echo Current config:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   Input backend: %SKY_INPUT_BACKEND%
echo   OCR model: PP-OCRv5 (device: %SKY_OCR_DEVICE%)
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
