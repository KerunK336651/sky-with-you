@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Sky Loop v7.1 (内存读取模式)

:: 检查管理员权限
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ========================================
    echo   Sky Loop v7.1 - 内存读取模式
    echo ========================================
    echo.
    echo 内存读取需要管理员权限。
    echo 即将请求管理员权限，请在 UAC 弹窗中点击"是"。
    echo.
    timeout /t 2 /nobreak >nul
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ========================================
echo   Sky Loop v7.1
echo   Chat engine: sky-companion port
echo   Input backend: Arduino (auto port)
echo   Chat input: Memory Reader
echo ========================================
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-flash
set SKY_VISION_ENABLED=1
set SKY_VISION_PROVIDER=deepseek
set SKY_INPUT_BACKEND=arduino
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=珂珂,幺幺,阿颜
set SKY_MEM_READER=1

echo Current config:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   Input backend: %SKY_INPUT_BACKEND%
echo   Chat input: Memory Reader (需要管理员权限)
echo   Web search: %SKY_SEARCH_ENABLED%
echo   Whitelist: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
echo   Player: 珂珂  AI: 星河
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
