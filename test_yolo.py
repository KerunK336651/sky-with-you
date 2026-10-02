# -*- coding: utf-8 -*-
"""
YOLOv8 实时推理测试脚本
功能：截取游戏窗口，用训练好的YOLO模型识别，显示结果
用法：
  1. 先运行 train_yolo.py 训练模型
  2. 运行: py test_yolo.py
  3. 按 q 退出
"""
import cv2
import numpy as np
import mss
import time
import ctypes
import os
import sys

# ========== 配置 ==========
MODEL_PATH = r"runs/detect/runs/detect/train/weights/best.pt"  # 训练好的模型路径
GAME_WINDOW_TITLE = "光·遇"
CONF_THRESHOLD = 0.75   # 置信度阈值，提高减少误检
# ==========================

GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
VK_Q = 0x51

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

def check_q():
    return GetAsyncKeyState(VK_Q) & 0x0001 != 0

def main():
    print("=" * 50)
    print("YOLOv8 实时推理测试")
    print("=" * 50)
    
    # 检查模型
    if not os.path.exists(MODEL_PATH):
        print(f"错误: 找不到模型 {MODEL_PATH}")
        print("请先运行 train_yolo.py 训练模型")
        sys.exit(1)
    
    # 检查 ultralytics
    try:
        from ultralytics import YOLO
    except ImportError:
        print("正在安装 ultralytics...")
        os.system(f"{sys.executable} -m pip install ultralytics")
        from ultralytics import YOLO
    
    # 加载模型
    print(f"加载模型: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    print("模型加载完成")
    
    # 查找游戏窗口
    win = find_game_window()
    if win:
        left, top, w, h = win
        print(f"找到游戏窗口: ({left},{top}) {w}x{h}")
    else:
        print(f"未找到游戏窗口 '{GAME_WINDOW_TITLE}'，截取主显示器")
        left, top = 0, 0
        w = ctypes.windll.user32.GetSystemMetrics(0)
        h = ctypes.windll.user32.GetSystemMetrics(1)
    
    print(f"\n按 Q 退出")
    print("=" * 50)
    
    sct = mss.mss()
    frame_count = 0
    last_fps_time = time.time()
    fps = 0
    
    try:
        while True:
            if check_q():
                print("\nQ 退出")
                break
            
            # 截屏
            monitor = {"left": left, "top": top, "width": w, "height": h}
            img = np.array(sct.grab(monitor))
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            # YOLO 推理
            results = model(frame, conf=CONF_THRESHOLD, verbose=False)
            
            # 绘制结果
            annotated = results[0].plot()
            
            # 计算 FPS
            frame_count += 1
            now = time.time()
            if now - last_fps_time > 1.0:
                fps = frame_count / (now - last_fps_time)
                frame_count = 0
                last_fps_time = now
            
            # 显示 FPS
            cv2.putText(annotated, f"FPS: {fps:.1f}", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            
            # 显示结果
            cv2.imshow("YOLO Detection (按Q退出)", annotated)
            
            # 打印检测到的目标
            if len(results[0].boxes) > 0:
                for box in results[0].boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    name = results[0].names[cls]
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    print(f"  检测到: {name} 置信度={conf:.2f} 位置=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})")
            
            # 按 ESC 也可以退出
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
    except KeyboardInterrupt:
        print("\nCtrl+C 退出")
    finally:
        cv2.destroyAllWindows()
        print("程序已退出")

if __name__ == "__main__":
    main()
