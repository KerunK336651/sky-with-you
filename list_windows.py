# -*- coding: utf-8 -*-
"""
列出所有可见窗口标题，帮助识别光遇游戏窗口
"""
import ctypes

EnumWindows = ctypes.windll.user32.EnumWindows
GetWindowTextW = ctypes.windll.user32.GetWindowTextW
GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
IsWindowVisible = ctypes.windll.user32.IsWindowVisible
GetWindowRect = ctypes.windll.user32.GetWindowRect

# 必须先声明 DPI 感知，否则量到的是逻辑像素（125%/150% 缩放下与实际像素不符）。
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

windows = []

def callback(hwnd, _):
    if not IsWindowVisible(hwnd):
        return True
    length = GetWindowTextLengthW(hwnd)
    if length == 0:
        return True
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    rect = RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    # 只列出有一定大小的窗口（排除小弹窗）
    if w > 200 and h > 200:
        windows.append((title, rect.left, rect.top, w, h))
    return True

EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback), 0)

print("=" * 60)
print("所有可见窗口（宽度>200, 高度>200）:")
print("=" * 60)
for i, (title, l, t, w, h) in enumerate(windows):
    print(f"[{i:2d}] {title[:50]:50s} 位置=({l},{t}) 大小={w}x{h}")
print("=" * 60)
print(f"共 {len(windows)} 个窗口")
print()
print("请找到光遇游戏窗口的标题，告诉我编号或标题关键词")
