@echo off
cd /d "%~dp0"
title Sky MCP Server (无硬件 - pydirectinput)
echo ========================================
echo   Sky MCP Server - 无硬件模式
echo   后端: pydirectinput（软件模拟键盘）
echo   端口: 9900
echo ========================================
echo.
echo 注意: 光遇PC版可能屏蔽软件模拟按键
echo 但 OCR/LLM/记忆/搜索 等聊天引擎逻辑正常
echo.
py sky-mcp-server.py --http --port 9900 --token 1234 --input-backend pydirectinput
echo.
echo [MCP exited with code %ERRORLEVEL%]
pause
