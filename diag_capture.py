# -*- coding: utf-8 -*-
"""
截图后端真机诊断（15 秒，零风险，不保存任何采集数据）
1. 运行后会先倒计时 5 秒，请立刻切到游戏；
2. 听到长音后，在游戏里持续【大幅度转动镜头 / 跑动】15 秒；
3. 结束会自动生成 _tmp/diag/compare.png 和 diffs.txt，
   上面一行是旧的 mss/GDI，下面一行是 DXGI，谁跟着画面动一目了然。
"""
import os
import sys
import time
import ctypes
import numpy as np
import cv2
import mss
import winsound

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ctypes.windll.user32.SetProcessDPIAware()
import bettercam

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "_tmp", "diag")
os.makedirs(OUT, exist_ok=True)

cam = bettercam.create(output_idx=0, output_color="BGR")
sct = mss.mss()
mon = sct.monitors[1]


def grab_gdi():
    s = sct.grab(mon)
    img = np.frombuffer(s.raw, np.uint8).reshape(s.height, s.width, 4)
    return np.ascontiguousarray(img[:, :, :3])


def grab_dxgi():
    f = cam.grab()
    if f is None:
        for _ in range(25):
            f = cam.grab()
            if f is not None:
                break
            time.sleep(0.02)
    return f


def small(im):
    return cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (64, 36)).astype(np.float32)


def lab(im, text):
    t = im.copy()
    cv2.rectangle(t, (0, 0), (200, 28), (0, 0, 0), -1)
    cv2.putText(t, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return t


print("5 秒后开始，请切到游戏窗口...")
for i in range(5, 0, -1):
    print("  %d" % i)
    winsound.Beep(800, 150)
    time.sleep(1)
winsound.Beep(1200, 450)
print("=== 开始！请立刻持续转动镜头/跑动 15 秒 ===")

g0, d0 = grab_gdi(), grab_dxgi()
if d0 is None:
    d0 = g0
pg, pd = small(g0), small(d0)
gdiff, ddiff = [], []
gT, dT = [], []
N = 15
for k in range(1, N + 1):
    time.sleep(1)
    g, d = grab_gdi(), grab_dxgi()
    cg = small(g)
    gdiff.append(float(np.abs(cg - pg).mean()))
    pg = cg
    if d is not None:
        cd = small(d)
        ddiff.append(float(np.abs(cd - pd).mean()))
        pd = cd
    else:
        ddiff.append(-1.0)
    if k in (1, 5, 10, 15):
        gT.append(lab(cv2.resize(g, (320, 180)), "GDI %d" % k))
        td = cv2.resize(d, (320, 180)) if d is not None else np.full((180, 320, 3), 30, np.uint8)
        dT.append(lab(td, "DXGI %d" % k))
    winsound.Beep(900, 30)
    print("frame %2d  GDI变化=%6.2f  DXGI变化=%6.2f" % (k, gdiff[-1], ddiff[-1]))

winsound.Beep(500, 500)
with open(os.path.join(OUT, "diffs.txt"), "w", encoding="utf-8") as f:
    f.write("GDI  " + " ".join("%.2f" % x for x in gdiff) + "\n")
    f.write("DXGI " + " ".join("%.2f" % x for x in ddiff) + "\n")
sheet = np.vstack([np.hstack(gT), np.hstack(dT)])
cv2.imwrite(os.path.join(OUT, "compare.png"), sheet)
cam.release()
print("DONE")
print("GDI 帧差:", ["%.2f" % x for x in gdiff])
print("DXGI帧差:", ["%.2f" % x for x in ddiff])
