@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (ALL FEATURES - no wing pickup / no teleport)
echo ========================================
echo   Sky Loop v7.1  全功能测试模式
echo   (除 光翼拾取 / 传送 外全部启用)
echo   Input backend: Arduino (auto port)
echo   Supervised: on crash -^> auto fallback to CPU OCR
echo ========================================
echo.

set SKY_LLM_PROVIDER=deepseek
set SKY_LLM_MODEL=deepseek-flash
set SKY_VISION_ENABLED=1
set SKY_VISION_PROVIDER=deepseek
set SKY_INPUT_BACKEND=arduino
set SKY_OCR_MODEL=v5
set SKY_OCR_DEVICE=auto
set SKY_SEARCH_ENABLED=1
set SKY_WHITELIST_ENABLED=1
set SKY_WHITELIST=珂珂,幺幺,阿颜

rem ===== 全功能（2026-11-01 测试用） =====
rem 自动接受好友互动邀请（含 26 公主抱 / 27 双人舞）；15s 兜底
set SKY_ACCEPT_AUTO=1
rem 设置菜单误开自动关（3s / 15s 补按，最多 3 次）
set SKY_SETTINGS_GUARD=1
rem 光翼硬门保留（有光翼附近时拦 F）
set SKY_WING_LIGHT_GUARD=1
rem 弹琴：拟人化 0=精确（可现场改 1/2 对比）
set SKY_SONG_HUMANIZE=0
rem AI 对话转储（logs\ai_trace，排查用）
set SKY_AI_TRACE=1

rem ===== 明确关闭（测试范围外，别打开） =====
rem 拾取光翼：无翼号绝不能开（捡了不可逆）
set SKY_AUTO_PICKUP_WING=0
rem 自动接受传送：本次不测传送
set SKY_ACCEPT_TELEPORT=0

echo Current config:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   Input backend: %SKY_INPUT_BACKEND%
echo   OCR model: PP-OCRv5 (device: %SKY_OCR_DEVICE%)
echo   Web search: %SKY_SEARCH_ENABLED%
echo   Whitelist: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
echo   ACCEPT_AUTO=%SKY_ACCEPT_AUTO%  SETTINGS_GUARD=%SKY_SETTINGS_GUARD%
echo   WING_LIGHT_GUARD=%SKY_WING_LIGHT_GUARD%  PICKUP_WING=%SKY_AUTO_PICKUP_WING%  TELEPORT=%SKY_ACCEPT_TELEPORT%
echo   SONG_HUMANIZE=%SKY_SONG_HUMANIZE%  AI_TRACE=%SKY_AI_TRACE%
echo   Player: 珂珂  AI: 星河
echo.

if not exist key.txt (
    echo [WARNING] key.txt not found
    echo Please create key.txt with your API key
    echo.
)

echo [1/3] Checking python...
py --version
echo.
echo [2/3] Checking MCP server (127.0.0.1:9900)...
py -c "import socket;s=socket.socket();s.settimeout(0.8);raise SystemExit(0 if s.connect_ex(('127.0.0.1',9900))==0 else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] MCP server is NOT running on 127.0.0.1:9900.
    echo         Starting start_mcp.bat automatically, waiting 4s...
    start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
    timeout /t 4 /nobreak >nul
) else (
    echo  [OK] MCP server is running.
)
echo.
echo [3/3] Starting main loop under supervisor (py run_loop.py)...
py run_loop.py 2>&1
echo.
echo [Loop exited with code %ERRORLEVEL%]
pause