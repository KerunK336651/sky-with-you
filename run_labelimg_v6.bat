@echo off
chcp 65001 >nul
title v6 YOLO labelImg
"C:\Users\86187\AppData\Local\Programs\Python\Python313\Scripts\labelImg.exe" "D:\sky_v6_label" "D:\sky_v6_label\classes.txt" "D:\sky_v6_label"
pause
