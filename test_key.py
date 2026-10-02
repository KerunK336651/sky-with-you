# -*- coding: utf-8 -*-
"""
按键测试脚本：测试 pydirectinput / pyautogui 在光遇中是否生效
用法：
  1. 先打开光遇，进入游戏世界
  2. 运行此脚本：py test_key.py
  3. 脚本会自动找到光遇窗口并聚焦，然后按 C 键（打开聊天面板）
  4. 观察游戏中是否打开了聊天面板
"""
import time
import sys

print("=" * 50)
print("  按键测试脚本")
print("=" * 50)

# 测试 1: 查找游戏窗口
print("\n[1/4] 查找光遇窗口...")
try:
    import pygetwindow as gw
    titles = ["光·遇", "光遇", "Sky", "Sky: Children of the Light", "Sky Children of the Light"]
    win = None
    for t in titles:
        matches = gw.getWindowsWithTitle(t)
        if matches:
            win = matches[0]
            print(f"  找到窗口: '{win.title}' ({win.width}x{win.height})")
            break
    if not win:
        print("  [错误] 没找到光遇窗口！请确认游戏已启动。")
        print("  当前所有窗口标题:")
        for w in gw.getAllWindows():
            if w.title and w.width > 100:
                print(f"    - '{w.title}'")
        sys.exit(1)
except ImportError:
    print("  [错误] 缺少 pygetwindow，请运行: py -m pip install pygetwindow")
    sys.exit(1)

# 测试 2: 聚焦窗口
print("\n[2/4] 聚焦游戏窗口...")
try:
    if win.isMinimized:
        win.restore()
    win.activate()
    time.sleep(0.5)
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    print(f"  当前前台窗口: '{buf.value}'")
    if "Sky" not in buf.value and "光" not in buf.value and "sky" not in buf.value.lower():
        print("  [警告] 前台窗口不是光遇！尝试强制聚焦...")
        # 强制聚焦
        hwnd_val = win._hWnd if hasattr(win, '_hWnd') else None
        if hwnd_val:
            user32.ShowWindow(hwnd_val, 9)
            user32.SetForegroundWindow(hwnd_val)
            time.sleep(0.3)
            user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 512)
            print(f"  强制聚焦后前台窗口: '{buf.value}'")
except Exception as e:
    print(f"  [错误] 聚焦失败: {e}")

# 测试 3: pydirectinput 按键
print("\n[3/4] 测试 pydirectinput 按键（3秒后按 C 键）...")
print("  请观察游戏中是否打开了聊天面板！")
for i in range(3, 0, -1):
    print(f"  {i}...")
    time.sleep(1)

try:
    import pydirectinput
    pydirectinput.PAUSE = 0.01
    print("  按下 C 键...")
    pydirectinput.keyDown('c')
    time.sleep(0.15)
    pydirectinput.keyUp('c')
    print("  C 键已发送（pydirectinput）")
    time.sleep(1)
    # 再按一次 C 关闭面板
    print("  再按 C 键关闭面板...")
    pydirectinput.keyDown('c')
    time.sleep(0.15)
    pydirectinput.keyUp('c')
except Exception as e:
    print(f"  [错误] pydirectinput 失败: {e}")

# 测试 4: pyautogui 按键
print("\n[4/4] 测试 pyautogui 按键（3秒后按 C 键）...")
print("  请观察游戏中是否打开了聊天面板！")
for i in range(3, 0, -1):
    print(f"  {i}...")
    time.sleep(1)

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.01
    print("  按下 C 键...")
    pyautogui.keyDown('c')
    time.sleep(0.15)
    pyautogui.keyUp('c')
    print("  C 键已发送（pyautogui）")
    time.sleep(1)
    print("  再按 C 键关闭面板...")
    pyautogui.keyDown('c')
    time.sleep(0.15)
    pyautogui.keyUp('c')
except Exception as e:
    print(f"  [错误] pyautogui 失败: {e}")

print("\n" + "=" * 50)
print("  测试完成！")
print("  如果游戏中聊天面板有打开/关闭，说明按键生效。")
print("  如果完全没反应，说明光遇屏蔽了软件模拟按键，")
print("  需要使用 Arduino 硬件键盘（约20元）。")
print("=" * 50)
input("\n按回车键退出...")
