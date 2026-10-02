# -*- coding: utf-8 -*-
"""检测 flip_debug 帧是否被 控制台/桌面 污染（非干净游戏画面）。只读分析。"""
import os, re, glob
import numpy as np, cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
FLIP = os.path.join(ROOT, "flip_debug")
pat = re.compile(r"chat_\d+_(on|off)_b(-?\d+)_t(-?\d+)\.jpg")

rows = []
for p in sorted(glob.glob(os.path.join(FLIP, "chat_*.jpg"))):
    mm = pat.search(os.path.basename(p))
    if not mm: continue
    g = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    h, w = g.shape
    black20 = float(np.mean(g < 20))    # 近纯黑占比（控制台 #0c0c0c；光遇夜景是深蓝不是纯黑）
    black32 = float(np.mean(g < 32))
    white = float(np.mean(g > 200))     # 高亮白（控制台文字 / 游戏白云都可能）
    bottom = g[int(h*0.965):h, :]
    bot_dark = float(np.mean(bottom < 45))   # 底部任务栏/横条近黑占比
    bot_mean = float(bottom.mean())
    rows.append((mm.group(1), black20, black32, white, bot_dark, bot_mean, os.path.basename(p)))

print(f"{'tag':3} {'blk20':>6} {'blk32':>6} {'white':>6} {'botDk':>6} {'botMn':>6}  name")
for r in sorted(rows, key=lambda x:(x[0], -x[2])):
    print(f"{r[0]:3} {r[1]:6.3f} {r[2]:6.3f} {r[3]:6.3f} {r[4]:6.3f} {r[5]:6.1f}  {r[6]}")

# 分位数看 blk32 是否能一刀切
for tag in ("on","off"):
    v=sorted(r[2] for r in rows if r[0]==tag)
    print(f"\n{tag} blk32 升序: "+" ".join(f"{x:.2f}" for x in v))
