# -*- coding: utf-8 -*-
"""
test_arduino_loop.py — Arduino 硬件输入闭环测试（不需要游戏）

验证和游戏里完全一致的输入链路：
  自动探测串口 -> 连接/清悬键 -> PING -> 单键 ->
  剪贴板写中文 -> Arduino 发 Ctrl+V 粘贴 -> Enter 回车 ->
  Ctrl+A / Ctrl+C 读回记事本内容自动比对。

会自动打开一个“记事本”当靶子，测试期间请不要动鼠标键盘（约 10 秒）。
结束后记事本保留，可肉眼核对；你的原剪贴板内容会被还原。

用法:  py test_arduino_loop.py
"""
import os
import sys
import time
import subprocess

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, detail)


def find_arduino():
    from serial.tools import list_ports
    known = {0x2341, 0x1B4F, 0x239A}
    ps = list(list_ports.comports())
    for p in ps:
        if p.vid in known or "arduino" in (p.description or "").lower():
            return p.device, ps
    ch = [p for p in ps if p.vid == 0x1A86]
    if len(ch) == 1:
        return ch[0].device, ps
    return None, ps


# Arduino USB HID 键码（与 sky-mcp-server 一致）
KC_CTRL, KC_A, KC_C, KC_V, KC_ENTER = 128, 97, 99, 118, 176


def main():
    import serial
    import pyperclip

    print("=" * 56)
    print("Arduino 硬件输入闭环测试（记事本当靶子）")
    print("=" * 56)

    # 1) 自动探测
    port, allports = find_arduino()
    print("当前串口:", [p.device for p in allports])
    check("自动探测到 Arduino", bool(port), "没找到，检查 USB")
    if not port:
        sys.exit(1)
    print("  ->", port)

    # 2) 打开记事本并置前
    print("\n[准备] 打开记事本，请勿动键鼠...")
    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1.8)
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getAllWindows()
                if ("记事本" in w.title or "notepad" in w.title.lower())]
        if wins:
            w = wins[0]
            try:
                if w.isMinimized:
                    w.restore()
                w.activate()
            except Exception:
                pass
            time.sleep(0.6)
            print("  记事本窗口:", w.title)
    except Exception as e:
        print("  窗口激活走默认:", e)

    saved_clip = ""
    try:
        saved_clip = pyperclip.paste()
    except Exception:
        pass

    ser = None
    try:
        # 3) 连接（开串口 DTR 会复位板子，等它报就绪）
        ser = serial.Serial(port, 115200, timeout=5)
        ready = ser.readline().decode(errors="replace").strip()
        check("串口连接+固件就绪", bool(ready), f"ready={ready!r}")
        print("  固件:", ready)

        def cmd(line, wait=0.12):
            ser.write(line.encode())
            r = ser.readline().decode(errors="replace").strip()
            time.sleep(wait)
            return r

        check("RELEASE 清悬键", cmd("RELEASE\n") in ("OK", "ERR", ""))
        check("PING 有响应", bool(cmd("PING\n")))

        # 4) 英文粘贴 + 回车
        en = "SkyArduinoOK123"
        pyperclip.copy(en); time.sleep(0.08)
        r1 = cmd(f"HOTKEY {KC_CTRL} {KC_V} 50\n")   # Ctrl+V
        cmd(f"PRESS {KC_ENTER} 80\n")               # Enter
        check("Ctrl+V 英文(固件响应OK)", r1 == "OK", r1)

        # 5) 中文粘贴 + 回车（游戏里发中文就是这条链路）
        zh = "星河测试中文输入abc"
        pyperclip.copy(zh); time.sleep(0.08)
        r2 = cmd(f"HOTKEY {KC_CTRL} {KC_V} 50\n")
        cmd(f"PRESS {KC_ENTER} 80\n")
        check("Ctrl+V 中文(固件响应OK)", r2 == "OK", r2)
        time.sleep(0.4)

        # 6) 读回记事本：Ctrl+A 全选 -> Ctrl+C 复制 -> pyperclip 读
        cmd(f"HOTKEY {KC_CTRL} {KC_A} 50\n"); time.sleep(0.15)
        cmd(f"HOTKEY {KC_CTRL} {KC_C} 50\n"); time.sleep(0.3)
        got = pyperclip.paste()
        print("  记事本读回内容:", repr(got))
        check("读回包含英文", en in got, got)
        check("读回包含中文", zh in got, got)
        # 两行都在，说明 Enter 也生效（出现换行）
        check("回车生效(含换行)", ("\n" in got or "\r" in got), got)

        cmd("RELEASE\n")
    finally:
        # 还原剪贴板
        try:
            pyperclip.copy(saved_clip)
        except Exception:
            pass
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    print("\n" + "=" * 56)
    print(f"结果: PASS={PASS} FAIL={FAIL}")
    print("记事本已保留，可肉眼核对里面是否有两行测试文字。")
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
