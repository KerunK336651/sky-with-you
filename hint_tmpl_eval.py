# -*- coding: utf-8 -*-
"""验证候选主锚点=底部按键提示行(ESC退后/ENTER聊天/T语音输入)。
从一张干净的真·开帧裁出提示行固定区域做模板，到所有帧同位置算归一化相关分。只读。"""
import os, re, glob
import numpy as np, cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
FLIP = os.path.join(ROOT, "flip_debug")
HINT = (0.0, 0.950, 0.32, 0.999)
pat = re.compile(r"chat_\d+_(on|off)_b.+\.jpg")

def crop_hint(path):
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    h, w = g.shape
    return g[int(h*HINT[1]):int(h*HINT[3]), int(w*HINT[0]):int(w*HINT[2])]

# 模板：180001 是干净的真·开面板（夜晚遇境，底部提示行清晰）
tmpl = crop_hint(os.path.join(FLIP, "chat_180001_on_b100_t3.jpg"))
print("模板尺寸:", tmpl.shape[::-1])

def score(roi):
    if roi.shape != tmpl.shape:
        roi = cv2.resize(roi, (tmpl.shape[1], tmpl.shape[0]))
    return float(cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)[0,0])

rows=[]
for p in sorted(glob.glob(os.path.join(FLIP,"chat_*.jpg"))):
    mm=pat.search(os.path.basename(p))
    if not mm: continue
    rows.append((mm.group(1), score(crop_hint(p)), os.path.basename(p)))

print(f"{'tag':3} {'hint相关分':>9}  name")
for r in sorted(rows,key=lambda x:(x[0],-x[1])):
    print(f"{r[0]:3} {r[1]:9.3f}  {r[2]}")
on=sorted(r[1] for r in rows if r[0]=='on'); off=sorted(r[1] for r in rows if r[0]=='off')
print(f"\nON  min={min(on):.3f} max={max(on):.3f}\nOFF min={min(off):.3f} max={max(off):.3f}  间隔on_min-off_max={min(on)-max(off):.3f}")
