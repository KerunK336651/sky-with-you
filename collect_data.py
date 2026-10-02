# -*- coding: utf-8 -*-
"""
YOLO 训练数据采集脚本
功能：按快捷键自动截取光遇游戏窗口，保存为图片
用法：
  F9  - 截取一张游戏窗口图片
  F10 - 开始/停止自动连拍（每2秒一张）
  F12 - 退出

截图保存在 D:\光遇截图 目录下
"""
import cv2
import numpy as np
import mss
import time
import ctypes
import os
from datetime import datetime

# ========== 配置 ==========
GAME_WINDOW_TITLE = "光·遇"
SAVE_DIR = r"D:\光遇截图"
AUTO_INTERVAL = 2.0  # 自动连拍间隔（秒）

GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
VK_F9 = 0x78
VK_F10 = 0x79
VK_F12 = 0x7B

FindWindowW = ctypes.windll.user32.FindWindowW
GetWindowRect = ctypes.windll.user32.GetWindowRect
IsWindowVisible = ctypes.windll.user32.IsWindowVisible

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def find_game_window():
    hwnd = FindWindowW(None, GAME_WINDOW_TITLE)
    if hwnd and IsWindowVisible(hwnd):
        rect = RECT()
        GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    return None

def check_key(vk):
    return GetAsyncKeyState(vk) & 0x0001 != 0

def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    
    print("=" * 50)
    print("YOLO 训练数据采集脚本")
    print("=" * 50)
    
    win = find_game_window()
    if win:
        left, top, w, h = win
        print(f"找到游戏窗口: {GAME_WINDOW_TITLE}")
        print(f"窗口位置: ({left},{top}) 大小: {w}x{h}")
    else:
        print(f"未找到游戏窗口 '{GAME_WINDOW_TITLE}'，将截取主显示器")
        left, top = 0, 0
        w = ctypes.windll.user32.GetSystemMetrics(0)
        h = ctypes.windll.user32.GetSystemMetrics(1)
    
    print(f"\n截图保存在: {os.path.abspath(SAVE_DIR)}")
    print(f"\n操作说明:")
    print(f"  F9  - 截取一张")
    print(f"  F10 - 开始/停止自动连拍（每{AUTO_INTERVAL}秒一张）")
    print(f"  F12 - 退出")
    print("=" * 50)
    
    sct = mss.mss()
    auto_mode = False
    last_auto_time = 0
    count = 0
    
    try:
        while True:
            now = time.time()
            
            # F9 单张截图
            if check_key(VK_F9):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"{SAVE_DIR}/sky_{timestamp}.png"
                monitor = {"left": left, "top": top, "width": w, "height": h}
                img = np.array(sct.grab(monitor))
                frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                success, encoded = cv2.imencode('.png', frame)
                if success:
                    with open(filename, 'wb') as f:
                        f.write(encoded.tobytes())
                    count += 1
                    print(f"[{count}] 已保存: {filename}")
                else:
                    print(f"保存失败: {filename}")
            
            # F10 自动连拍开关
            if check_key(VK_F10):
                auto_mode = not auto_mode
                status = "开始" if auto_mode else "停止"
                print(f"\n自动连拍 {status}")
            
            # 自动连拍
            if auto_mode and now - last_auto_time > AUTO_INTERVAL:
                last_auto_time = now
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"{SAVE_DIR}/sky_{timestamp}.png"
                monitor = {"left": left, "top": top, "width": w, "height": h}
                img = np.array(sct.grab(monitor))
                frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                success, encoded = cv2.imencode('.png', frame)
                if success:
                    with open(filename, 'wb') as f:
                        f.write(encoded.tobytes())
                    count += 1
                    print(f"[{count}] 自动: {filename}")
                else:
                    print(f"保存失败: {filename}")
            
            # F12 退出
            if check_key(VK_F12):
                print(f"\n共采集 {count} 张图片")
                print("退出")
                break
            
            time.sleep(0.05)
            
    except KeyboardInterrupt:
        print(f"\n共采集 {count} 张图片")
        print("退出")

if __name__ == "__main__":
    main()
