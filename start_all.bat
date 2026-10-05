@echo off
cd /d "%~dp0"
title Sky With You 一键启动

rem ====== 自我提权（软件按键/内存读取需要管理员；Arduino 模式提了也无妨） ======
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ========================================
    echo   Sky With You 一键启动
    echo ========================================
    echo.
    echo 即将请求管理员权限，请在 UAC 弹窗点“是”。
    timeout /t 2 /nobreak >nul
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:menu
cls
echo ========================================
echo   Sky With You 一键启动（选择模式）
echo ========================================
echo.
echo   [1] 正常启动：deepseek-flash 一个模型全包（聊天+视觉，OCR/Arduino）   默认
echo   [2] 正常启动 + AI对话转储（logs\ai_trace，排查AI输入输出）
echo   [3] 内存读取模式：硬件MCP + mem主循环（需管理员）
echo   [4] 只启动 MCP 服务器（主循环想自己手动开时用）
echo   [5] 无硬件演示模式（软件按键，光遇可能屏蔽，仅调试）
echo   [6] （同[1]，视觉已并入正常模式）deepseek-flash 视觉校验
echo   [7] （同[2]）视觉校验 + AI对话转储
echo   [0] 退出
echo.
set "choice="
set /p choice=请输入选项后回车（直接回车=1）:
if "%choice%"=="" set "choice=1"
if "%choice%"=="1" goto normal
if "%choice%"=="2" goto trace
if "%choice%"=="3" goto memmode
if "%choice%"=="4" goto mcponly
if "%choice%"=="5" goto nohw
if "%choice%"=="6" goto vision
if "%choice%"=="7" goto visiontrace
if "%choice%"=="0" exit /b
echo 无效选项，请重新输入。
timeout /t 1 /nobreak >nul
goto menu

:normal
echo [1/2] 启动 MCP 服务器...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
echo [2/2] 3 秒后启动主循环...
timeout /t 3 /nobreak >nul
start "Sky Loop" cmd /k "%~dp0start_loop.bat"
goto done

:trace
echo [1/2] 启动 MCP 服务器...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
echo [2/2] 3 秒后启动主循环（带 AI 转储）...
timeout /t 3 /nobreak >nul
start "Sky Loop (AI Trace)" cmd /k "%~dp0start_loop_trace.bat"
goto done

:memmode
echo [1/2] 启动 MCP 服务器...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
echo [2/2] 3 秒后启动主循环（内存读取）...
timeout /t 3 /nobreak >nul
start "Sky Loop (Memory)" cmd /k "%~dp0start_loop_mem.bat"
goto done

:mcponly
echo 启动 MCP 服务器（主循环请自行双击 start_loop.bat）...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
goto done

:nohw
echo 无硬件演示：启动软件按键 MCP + 无硬件主循环...
start "Sky MCP (NoHW)" cmd /k "%~dp0start_mcp_no_hardware.bat"
timeout /t 3 /nobreak >nul
start "Sky Loop (NoHW)" cmd /k "%~dp0start_loop_no_hardware.bat"
goto done

:vision
echo [1/2] 启动 MCP 服务器...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
echo [2/2] 3 秒后启动主循环（带 VLM 视觉校验）...
timeout /t 3 /nobreak >nul
start "Sky Loop (Vision)" cmd /k "%~dp0start_loop_vision.bat"
goto done

:visiontrace
echo [1/2] 启动 MCP 服务器...
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
echo [2/2] 3 秒后启动主循环（视觉校验 + AI转储）...
timeout /t 3 /nobreak >nul
start "Sky Loop (Vision+Trace)" cmd /k "%~dp0start_loop_vision_trace.bat"
goto done

:done
echo.
echo 已按所选模式拉起对应窗口，本窗口可以关闭。
timeout /t 6 /nobreak >nul
exit /b
