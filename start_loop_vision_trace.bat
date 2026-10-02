@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (视觉校验+AI转储)
echo ========================================
echo   Sky Loop v7.1 + VLM Pose Check + AI Trace
echo   视觉校验: qwen-vl-plus 姿势结果校验
echo   AI转储 : logs\ai_trace_*.txt（只留最近5份）
echo ========================================
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-v4-flash
set SKY_INPUT_BACKEND=arduino
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=珂珂,幺幺,阿颜
set SKY_VISION_ENABLED=1
set SKY_AI_TRACE=1

echo 当前配置:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   输入后端: %SKY_INPUT_BACKEND%
echo   联网搜索: %SKY_SEARCH_ENABLED%
echo   白名单: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
echo   视觉校验VLM: 开启 (qwen-vl-plus)
echo   AI对话转储: 开启 -^> logs\ai_trace_*.txt
echo   玩家: 珂珂  AI: 星河
echo.

if not exist key.txt (
    echo [警告] 未找到 key.txt
    echo.
)
if not exist vision_key.txt (
    echo [警告] 未找到 vision_key.txt，视觉校验会保持关闭
    echo.
)

echo [1/2] Python:
py --version
echo.
echo [2/2] 启动主循环（视觉校验 + AI转储）...
py sky-loop-v7.py 2>&1
echo.
echo [Loop exited with code %ERRORLEVEL%]
pause
