# -*- coding: utf-8 -*-
"""
视角锁定测试脚本 v6
改用键盘方向键转视角（光遇键位：方向键=查看），不抢鼠标
"""
import cv2
import numpy as np
import mss
import time
import ctypes
from rapidocr_onnxruntime import RapidOCR

# ========== 配置 ==========
TARGET_NAME = "珂珂"
KEY_DURATION = 100           # 方向键按下时长（毫秒）
DEAD_ZONE = 80                # 死区
OCR_INTERVAL = 0.2
VIEW_LOCK_ENABLED = False
DEBUG_OCR = True
GAME_WINDOW_TITLE = "光·遇"

GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
VK_F8 = 0x77

# 方向键虚拟键码
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28

keybd_event = ctypes.windll.user32.keybd_event
KEYEVENTF_KEYUP = 0x0002

def press_key(vk, duration_ms=100):
    """按下并释放一个键"""
    keybd_event(vk, 0, 0, 0)
    time.sleep(duration_ms / 1000.0)
    keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

FindWindowW = ctypes.windll.user32.FindWindowW
GetWindowRect = ctypes.windll.user32.GetWindowRect
IsWindowVisible = ctypes.windll.user32.IsWindowVisible

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

print("=" * 50)
print("视角锁定测试脚本 v6 (方向键转视角)")
print("=" * 50)

hwnd = FindWindowW(None, GAME_WINDOW_TITLE)
if hwnd and IsWindowVisible(hwnd):
    rect = RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    game_left = rect.left
    game_top = rect.top
    game_width = rect.right - rect.left
    game_height = rect.bottom - rect.top
    print(f"找到游戏窗口: {GAME_WINDOW_TITLE}")
    print(f"窗口位置: ({game_left},{game_top})")
    print(f"窗口大小: {game_width}x{game_height}")
else:
    print(f"未找到窗口 '{GAME_WINDOW_TITLE}'")
    game_left = game_top = 0
    game_width = ctypes.windll.user32.GetSystemMetrics(0)
    game_height = ctypes.windll.user32.GetSystemMetrics(1)

print(f"\n目标名字: {TARGET_NAME}")
print(f"按键时长: {KEY_DURATION}ms")
print(f"死区: {DEAD_ZONE}px")
print()
print("操作说明:")
print("  F8     - 切换视角锁定 开/关")
print("  Ctrl+C - 退出程序")
print()
print("注意: 用方向键转视角，不会抢鼠标")
print("      但需要游戏窗口在前台，且方向键确实能转视角")
print()
print("当前状态: 视角锁定 已关闭 (按 F8 开启)")
print("=" * 50)

ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
sct = mss.mss()

def check_f8():
    return GetAsyncKeyState(VK_F8) & 0x0001 != 0

def find_name_position(frame):
    result, _ = ocr(frame)
    if not result:
        return None
    
    if DEBUG_OCR and VIEW_LOCK_ENABLED:
        all_texts = []
        for item in result:
            if len(item) >= 3:
                all_texts.append(f"{item[1]}({float(item[2]):.2f})")
        if all_texts:
            print(f"  [OCR-DEBUG] 识别到: {' | '.join(all_texts[:5])}")
    
    best = None
    best_conf = 0
    h, w = frame.shape[:2]
    
    for item in result:
        if len(item) < 3:
            continue
        text = str(item[1]).strip()
        conf = float(item[2])
        
        if len(text) > 4:
            continue
        
        if "珂" in text:
            box = item[0]
            cx = (box[0][0] + box[2][0]) / 2
            cy = (box[0][1] + box[2][1]) / 2
            
            if cx < w * 0.30 and cy > h * 0.6:
                continue
            
            if conf > best_conf:
                best_conf = conf
                best = (cx, cy, conf, text)
    return best

def main():
    global VIEW_LOCK_ENABLED
    
    last_ocr_time = 0
    name_pos = None
    f8_was_pressed = False
    
    try:
        while True:
            now = time.time()
            
            f8_pressed = check_f8()
            if f8_pressed and not f8_was_pressed:
                VIEW_LOCK_ENABLED = not VIEW_LOCK_ENABLED
                status = "已开启" if VIEW_LOCK_ENABLED else "已关闭"
                print(f"\n[F8] 视角锁定 {status}")
            f8_was_pressed = f8_pressed
            
            if now - last_ocr_time > OCR_INTERVAL:
                last_ocr_time = now
                
                crop_height = int(game_height * 0.7)
                monitor_region = {
                    "left": game_left,
                    "top": game_top,
                    "width": game_width,
                    "height": crop_height
                }
                
                try:
                    img = np.array(sct.grab(monitor_region))
                    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                except Exception as e:
                    print(f"  [截屏错误] {e}")
                    time.sleep(0.5)
                    continue
                
                h, w = frame.shape[:2]
                found = find_name_position(frame)
                
                if found:
                    cx, cy, conf, text = found
                    name_pos = found
                    if VIEW_LOCK_ENABLED:
                        print(f"  [OCR] 找到 '{text}' 位置=({cx:.0f},{cy:.0f}) 置信度={conf:.2f}")
                else:
                    name_pos = None
                    if VIEW_LOCK_ENABLED:
                        print(f"  [OCR] 未找到名字")
            
            # 用方向键转视角
            if VIEW_LOCK_ENABLED and name_pos:
                cx, cy, conf, text = name_pos
                center_x = w * 0.5
                center_y = h * 0.5
                
                dx = cx - center_x
                dy = cy - center_y
                
                if abs(dx) > DEAD_ZONE:
                    if dx > 0:
                        # 名字在右边，按右键转视角（需要测试方向对不对）
                        press_key(VK_RIGHT, KEY_DURATION)
                        print(f"  [KEY] 名字在右边(dx={dx:.0f})，按→")
                    else:
                        press_key(VK_LEFT, KEY_DURATION)
                        print(f"  [KEY] 名字在左边(dx={dx:.0f})，按←")
                
                if abs(dy) > DEAD_ZONE:
                    if dy > 0:
                        # 名字在下边，按下键
                        press_key(VK_DOWN, KEY_DURATION)
                        print(f"  [KEY] 名字在下边(dy={dy:.0f})，按↓")
                    else:
                        press_key(VK_UP, KEY_DURATION)
                        print(f"  [KEY] 名字在上边(dy={dy:.0f})，按↑")
            
            time.sleep(0.05)
            
    except KeyboardInterrupt:
        print("\n\nCtrl+C 退出")
    finally:
        print("程序已退出")

if __name__ == "__main__":
    main()
