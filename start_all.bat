@echo off
cd /d "%~dp0"
title Sky With You 一键启动

rem ====== 自我提权（软件按键/部分模式需要管理员；Arduino 模式提了也无妨） ======
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
echo   [1] 正常启动：v7 28类 + PP-OCRv5 + 光翼硬门 + 弹琴 + 看护进程   默认
echo   [2] 同[1] + AI对话转储（logs\ai_trace，排查AI输入输出）
echo   [3] 只启动 MCP 服务器（主循环想自己手动开时用）
echo   [4] 无硬件演示模式（软件按键，光遇可能屏蔽，仅调试）
echo   [5] （同[1]，视觉已并入正常模式）deepseek-flash 视觉校验
echo   [6] （同[2]）视觉校验 + AI对话转储
echo   [7] Same as [1] + live YOLO preview window (see detect boxes)
echo   [8] Standalone YOLO preview ONLY (view only, no MCP/Arduino/AI)
echo   [9] 同[1] + 阶段3自动接受邀请（AI自己决定接不接，15s兜底）
echo   [10] 弹琴设置：快捷栏 1~8 / 乐器类型（玩家自己填，弹琴前跑一次）
echo   [11] 光翼拾取设置：账号有翼/自动拾取（默认都关，无翼号别开）
echo   [12] 全功能测试（除光翼拾取/传送）：自动接受邀请/设置守卫/弹琴/视觉
echo   注：[4][5][6] 是旧配置（OCR=v3、无看护），排查时才用
echo   [0] 退出

echo.
set "choice="
set /p choice=请输入选项后回车（直接回车=1）:
if "%choice%"=="" set "choice=1"
if "%choice%"=="1" goto normal
if "%choice%"=="2" goto trace
if "%choice%"=="3" goto mcponly
if "%choice%"=="4" goto nohw
if "%choice%"=="5" goto vision
if "%choice%"=="6" goto visiontrace
if "%choice%"=="7" goto preview
if "%choice%"=="8" goto previewonly
if "%choice%"=="9" goto accept3
if "%choice%"=="10" goto songsetup
if "%choice%"=="11" goto wingsetup
if "%choice%"=="12" goto alltest
if "%choice%"=="0" exit /b
echo 无效选项，请重新输入。
timeout /t 1 /nobreak >nul
goto menu

:normal
echo [1/2] 启动 MCP 服务器...
call :maybe_start_mcp
echo [2/2] 3 秒后启动主循环（PP-OCRv5 + 看护）...
timeout /t 3 /nobreak >nul
rem 弹琴槽位没填时只提示，不阻断启动（陪聊/互动不受影响）
py -c "import sys;sys.path.insert(0,'.');from core.song_settings import slot_of;sys.exit(0 if slot_of() else 1)" >nul 2>&1
if errorlevel 1 echo   [注意] 弹琴快捷栏还没填：菜单 [10] 或 setup_song.bat（不影响陪聊）
start "Sky Loop v5 (supervised)" cmd /k "%~dp0start_loop_v5.bat"
goto done

:trace
echo [1/2] 启动 MCP 服务器...
call :maybe_start_mcp
echo [2/2] 3 秒后启动主循环（PP-OCRv5 + 看护 + AI 转储）...
timeout /t 3 /nobreak >nul
set "SKY_AI_TRACE=1"
goto done


:mcponly
echo 启动 MCP 服务器（主循环请自行双击 start_loop.bat）...
call :maybe_start_mcp
goto done

:nohw
echo 无硬件演示：启动软件按键 MCP + 无硬件主循环...
call :maybe_start_mcp_nohw
timeout /t 3 /nobreak >nul
start "Sky Loop (NoHW)" cmd /k "%~dp0start_loop_no_hardware.bat"
goto done

:vision
echo [1/2] 启动 MCP 服务器...
call :maybe_start_mcp
echo [2/2] 3 秒后启动主循环（带 VLM 视觉校验）...
timeout /t 3 /nobreak >nul
start "Sky Loop (Vision)" cmd /k "%~dp0start_loop_vision.bat"
goto done

:visiontrace
echo [1/2] 启动 MCP 服务器...
call :maybe_start_mcp
echo [2/2] 3 秒后启动主循环（视觉校验 + AI转储）...
timeout /t 3 /nobreak >nul
start "Sky Loop (Vision+Trace)" cmd /k "%~dp0start_loop_vision_trace.bat"
goto done

:preview
echo [1/2] Starting MCP server...
call :maybe_start_mcp
echo [2/2] Starting loop WITH live YOLO preview (q/close stops preview only)...
timeout /t 3 /nobreak >nul
start "Sky Loop (YOLO Preview)" cmd /k "%~dp0start_loop_preview.bat"
goto done

:previewonly
echo Starting standalone YOLO preview (view only, no MCP/keys/AI)...
start "Sky YOLO Preview" cmd /k "%~dp0yolo_preview.bat"
goto done

:accept3
echo [1/2] 启动 MCP 服务...
call :maybe_start_mcp
echo [2/2] 阶段3自动接受邀请已启用（SKY_ACCEPT_AUTO=1），3秒后启动循环...
set SKY_ACCEPT_AUTO=1
timeout /t 3 /nobreak >nul
start "Sky Loop (阶段3)" cmd /k "%~dp0start_loop_v5.bat"
goto done

:songsetup
start "Sky Song Setup" cmd /k "%~dp0setup_song.bat"
goto done

:wingsetup
start "Sky Wing Setup" cmd /k "%~dp0setup_wing.bat"
goto done

:alltest
echo [1/2] 启动 MCP 服务器...
call :maybe_start_mcp
echo [2/2] 3 秒后启动主循环（全功能测试：除光翼拾取/传送）...
timeout /t 3 /nobreak >nul
start "Sky Loop (ALL TEST)" cmd /k "%~dp0start_loop_alltest.bat"
goto done

:done
echo.
echo 已按所选模式拉起对应窗口，本窗口可以关闭。
timeout /t 6 /nobreak >nul
exit /b

:maybe_start_mcp
netstat -ano | findstr "LISTENING" | findstr /R /C:":9900 " >nul 2>&1
if %errorlevel%==0 (
    echo [SKIP] MCP already running on port 9900, reusing it.
    goto :eof
)
start "Sky MCP Server" cmd /k "%~dp0start_mcp.bat"
goto :eof

:maybe_start_mcp_nohw
netstat -ano | findstr "LISTENING" | findstr /R /C:":9900 " >nul 2>&1
if %errorlevel%==0 (
    echo [SKIP] MCP already running on port 9900, reusing it.
    goto :eof
)
start "Sky MCP (NoHW)" cmd /k "%~dp0start_mcp_no_hardware.bat"
goto :eof
