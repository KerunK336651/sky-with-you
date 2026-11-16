@echo off
cd /d "%~dp0"
title Sky Loop v7.1 (PP-OCRv5 / auto)
echo ========================================
echo   Sky Loop v7.1  PP-OCRv5
echo   Chat engine: sky-companion port
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
rem Wing auto-pickup (default OFF): keep OFF for wingless accounts (irreversible);
rem for winged accounts, uncomment the line below to enable auto pickup.
rem set SKY_AUTO_PICKUP_WING=1
set SKY_WHITELIST=珂珂,幺幺,阿颜

rem ===== 弹琴（2026-11-01）：快捷栏/乐器在 setup_song.bat 里填，这里只管播放行为 =====
rem 拟人化：0=精确按谱（默认）1=轻微抖动+句间呼吸 2=明显（适合慢曲）
set SKY_SONG_HUMANIZE=0
rem 停止信号：每 2s 扫一次，连续 2 拍确认才停（约 4s）；改成 1 就是一拍立刻停
rem set SKY_SONG_STOP_CONFIRM=2
rem set SKY_SONG_STOP_SCAN=2.0
rem 乐器界面确认等待（秒）：真机认不出 instrument 就调大到 8
rem set SKY_INSTRUMENT_WAIT=4.0
rem 停止词留空则只认界面词：set SKY_SONG_STOP_WORDS=
rem 互动邀请：默认 0=只观测不按 F；确认稳定后再改 1（或菜单 [9]）
rem set SKY_ACCEPT_AUTO=0

echo Current config:
echo   LLM: %SKY_LLM_PROVIDER% / %SKY_LLM_MODEL%
echo   Input backend: %SKY_INPUT_BACKEND%
echo   OCR model: PP-OCRv5 (device: %SKY_OCR_DEVICE%)
echo   Web search: %SKY_SEARCH_ENABLED%
echo   Whitelist: %SKY_WHITELIST_ENABLED% (%SKY_WHITELIST%)
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
rem ====== MCP preflight: all key presses go through it, no MCP = chat panel never opens ======
echo [2/3] Checking MCP server (127.0.0.1:9900)...
py -c "import socket;s=socket.socket();s.settimeout(0.8);raise SystemExit(0 if s.connect_ex(('127.0.0.1',9900))==0 else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN] MCP server is NOT running on 127.0.0.1:9900.
    echo         Key presses are forwarded by MCP, so the chat panel would never open.
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
