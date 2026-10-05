@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (AI对话转储)

echo ========================================
echo   Sky Loop v7.1 + AI Trace
echo   每轮对话的【完整输入 / 模型原始返回 / 最终处理】
echo   会写到 logs\ai_trace_时间.txt（只保留最近5份）
echo   想关闭转储就用平时的 start_loop.bat
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
set SKY_AI_TRACE=1

echo 当前配置:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   AI对话转储: 开启 -^> logs\ai_trace_*.txt
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
echo [2/2] 启动主循环（带 AI 转储）...
py sky-loop-v7.py 2>&1
echo.
echo [Loop exited with code %ERRORLEVEL%]
pause
