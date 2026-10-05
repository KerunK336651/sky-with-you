@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (无硬件模式)
echo ========================================
echo   Sky Loop v7.1 - 无硬件模式
echo   聊天引擎: sky-companion 移植版
echo   按键后端: pydirectinput（软件模拟，光遇可能屏蔽）
echo ========================================
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-flash
set SKY_VISION_ENABLED=1
set SKY_VISION_PROVIDER=deepseek
set SKY_INPUT_BACKEND=pydirectinput
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=珂珂,幺幺,阿颜

echo 当前配置:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   按键后端: %SKY_INPUT_BACKEND%
echo   联网搜索: %SKY_SEARCH_ENABLED%
echo   白名单: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
echo   玩家: 珂珂  AI: 星河
echo.

if not exist key.txt (
    echo [警告] 未找到 key.txt，请在项目目录创建并写入 API Key
    echo.
)

echo [1/2] Python:
py --version
echo.
echo [2/2] 启动主循环（无硬件，软件按键）...
py sky-loop-v7.py 2>&1
echo.
echo [Loop exited with code %ERRORLEVEL%]
pause
