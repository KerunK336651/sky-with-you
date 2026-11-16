@echo off
chcp 936 >nul
title 检查新标注（7类）

set "ROOT=D:\光遇互动采集"
set "LI=C:\Users\86187\AppData\Local\Programs\Python\Python313\Scripts\labelImg.exe"

:menu
cls
echo ================================
echo      选择要检查的类别
echo ================================
echo  [1] action_cancel   持续动作取消  148
echo  [2] butterfly_lure  引蝶          135
echo  [3] duo_dance       双人舞        232
echo  [4] instrument      乐器界面      129
echo  [5] negative        困难负样本    112
echo  [6] princess_hug    公主抱        233
echo  [7] settings_menu   设置菜单      116
echo  [0] 退出
echo ================================
set /p choice=请输入数字后回车:

if "%choice%"=="1" goto c1
if "%choice%"=="2" goto c2
if "%choice%"=="3" goto c3
if "%choice%"=="4" goto c4
if "%choice%"=="5" goto c5
if "%choice%"=="6" goto c6
if "%choice%"=="7" goto c7
if "%choice%"=="0" exit
goto menu

:c1
"%LI%" "%ROOT%\action_cancel" "%ROOT%\action_cancel\classes.txt" "%ROOT%\action_cancel"
goto menu
:c2
"%LI%" "%ROOT%\butterfly_lure" "%ROOT%\butterfly_lure\classes.txt" "%ROOT%\butterfly_lure"
goto menu
:c3
"%LI%" "%ROOT%\duo_dance" "%ROOT%\duo_dance\classes.txt" "%ROOT%\duo_dance"
goto menu
:c4
"%LI%" "%ROOT%\instrument" "%ROOT%\instrument\classes.txt" "%ROOT%\instrument"
goto menu
:c5
"%LI%" "%ROOT%\negative" "%ROOT%\negative\classes.txt" "%ROOT%\negative"
goto menu
:c6
"%LI%" "%ROOT%\princess_hug" "%ROOT%\princess_hug\classes.txt" "%ROOT%\princess_hug"
goto menu
:c7
"%LI%" "%ROOT%\settings_menu" "%ROOT%\settings_menu\classes.txt" "%ROOT%\settings_menu"
goto menu