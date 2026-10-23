@echo off
cd /d "%~dp0"
title Sky Loop (YOLO Preview)
rem ============================================================
rem  Same as start_loop_v5.bat, but opens a live YOLO preview
rem  window showing every detected box (12 classes + conf).
rem  Close the window with q or X; the main loop keeps running.
rem ============================================================
set SKY_YOLO_PREVIEW=1
call "%~dp0start_loop_v5.bat"
