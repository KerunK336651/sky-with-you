# -*- coding: utf-8 -*-
"""
test_ocr_enhance.py — OCR 增强离线测试（无需开游戏）

验证 2026-09-01 新增的三块：
  1) _white_text_mask  白色文字掩码（压亮色/彩色背景）
  2) fix_vocab         光遇专有词保守纠错（词长>=3 才模糊纠错，防误伤）
  3) ChatVoteBuffer    跨帧在线投票（首帧不延迟，偶发错字被多数帧拉回）

另用合成的“光遇风格”聊天图（白字+深色描边，亮/暗两种背景）端到端跑
read_chat_ocr，并把各预处理变体导出到 _ocr_test_out/ 供肉眼对比。

注意：合成图用的是微软雅黑、不是光遇自带圆润字体，所以这里只验证
“管线不崩 + 方向正确 + 逻辑正确”，真实识别率以回家实机截图为准。

用法:  py test_ocr_enhance.py
"""
import os
import sys
import importlib.util

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import numpy as np
import cv2

# ── 按文件路径加载主程序（模块名带连字符，不能直接 import）──
spec = importlib.util.spec_from_file_location(
    "skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


print("=" * 60)
print("一、fix_vocab 专有词保守纠错")
print("=" * 60)
# 词长>=3：差1字应被纠正
check("暴风眼 错1字", M.fix_vocab("一起去暴凤眼吗").find("暴风眼") >= 0,
      M.fix_vocab("一起去暴凤眼吗"))
check("监护人 错1字", M.fix_vocab("监护人呢") == "监护人呢",
      M.fix_vocab("监护入呢"))
check("升华蜡烛 错1字", M.fix_vocab("升华腊烛").find("升华蜡烛") >= 0)
# 已正确的不变
check("正确句子不动", M.fix_vocab("去雨林收烛火") == "去雨林收烛火")
# 2字词不做模糊纠错，防止误伤正常用字
check("2字词不误伤(点个火)", M.fix_vocab("点个火吧") == "点个火吧",
      M.fix_vocab("点个火吧"))
check("2字词不误伤(云也保留)", M.fix_vocab("我在云也等你") == "我在云也等你",
      M.fix_vocab("我在云也等你"))
check("普通句不动", M.fix_vocab("今天天气真好啊") == "今天天气真好啊")

print("=" * 60)
print("二、ChatVoteBuffer 跨帧在线投票")
print("=" * 60)
v = M.ChatVoteBuffer(window=5.0)
# 首帧无历史：原样输出，不延迟、不瞎改
r1 = v.stabilize([{"text": "我在云野等你", "confidence": 0.9}], 0.0)
check("首帧原样输出", r1[0]["text"] == "我在云野等你", str(r1))
# 第2、3帧同样正确
v.stabilize([{"text": "我在云野等你", "confidence": 0.9}], 1.5)
v.stabilize([{"text": "我在云野等你", "confidence": 0.88}], 3.0)
# 第4帧偶发识别错（多一笔/错一字），历史3票正确，应被拉回
r4 = v.stabilize([{"text": "我在云野等你l", "confidence": 0.7}], 4.5)
check("偶发错字被多数票拉回", r4[0]["text"] == "我在云野等你", str(r4))
# 全新一行、无历史：原样
r5 = v.stabilize([{"text": "刚刚那是什么", "confidence": 0.8}], 4.6)
check("新行无历史原样", r5[0]["text"] == "刚刚那是什么", str(r5))
# 时间窗外的历史不参与
v2 = M.ChatVoteBuffer(window=1.0)
v2.stabilize([{"text": "AAAAAAAA", "confidence": 0.9}], 0.0)
ro = v2.stabilize([{"text": "AAAAAAAB", "confidence": 0.9}], 5.0)
check("超窗历史不投票", ro[0]["text"] == "AAAAAAAB", str(ro))

print("=" * 60)
print("三、_white_text_mask 掩码基本性质")
print("=" * 60)
# 纯黑图：白像素应极少；纯白图：应几乎全白
black = np.zeros((100, 100, 3), dtype=np.uint8)
white = np.full((100, 100, 3), 255, dtype=np.uint8)
mb = M._white_text_mask(black)
mw = M._white_text_mask(white)
check("黑底掩码白像素<5%", (mb > 0).mean() < 0.05, f"{(mb>0).mean():.3f}")
check("白底掩码白像素>95%", (mw > 0).mean() > 0.95, f"{(mw>0).mean():.3f}")
# 高饱和彩色块（如蓝天/绿地）应被压掉，不当成白字
color = np.zeros((100, 100, 3), dtype=np.uint8)
color[:] = (200, 120, 40)  # BGR 鲜艳蓝
mc = M._white_text_mask(color)
check("高饱和彩色被压制", (mc > 0).mean() < 0.10, f"{(mc>0).mean():.3f}")

# ── 合成光遇风格聊天图 ──
def make_chat_image(bright=True):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 760, 420
    img = Image.new("RGB", (W, H))
    px = img.load()
    # 背景渐变
    for y in range(H):
        for x in range(0, W, 4):
            if bright:  # 云野：偏亮暖色
                c = (215 - x // 40, 225 - y // 30, 235)
            else:       # 雨林：偏暗蓝绿
                c = (30 + x // 60, 45 + y // 40, 40)
            for k in range(4):
                if x + k < W:
                    px[x + k, y] = c
    dr = ImageDraw.Draw(img, "RGBA")
    # 亮色背景上加几个亮色/彩色干扰斑
    if bright:
        dr.ellipse([520, 30, 720, 200], fill=(245, 240, 220, 255))
        dr.ellipse([60, 260, 240, 400], fill=(230, 225, 200, 255))
        dr.ellipse([400, 250, 560, 380], fill=(210, 230, 160, 255))
    else:
        dr.ellipse([520, 40, 700, 200], fill=(30, 70, 90, 255))
    # 半透明深色聊天底条
    dr.rounded_rectangle([20, 60, 520, 360], radius=18,
                         fill=(20, 22, 30, 120))
    fp = r"C:\Windows\Fonts\msyh.ttc"
    font = ImageFont.truetype(fp, 30)
    lines = ["珂珂-你在哪里呀", "星河-我在云野等你", "珂珂-一起去暴风眼吗"]
    for i, t in enumerate(lines):
        dr.text((45, 95 + i * 80), t, font=font, fill=(255, 255, 255, 255),
                stroke_width=2, stroke_fill=(55, 55, 65, 255))
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return bgr


print("=" * 60)
print("四、合成聊天图端到端 read_chat_ocr")
print("=" * 60)
out_dir = os.path.join(ROOT, "_ocr_test_out")
os.makedirs(out_dir, exist_ok=True)
try:
    engine = M.make_ocr_engine()
    have_engine = True
except Exception as e:
    have_engine = False
    print("  RapidOCR 引擎不可用，跳过端到端：", e)

for tag, bright in (("bright_云野亮底", True), ("dark_雨林暗底", False)):
    frame = make_chat_image(bright)
    cv2.imwrite(os.path.join(out_dir, f"synth_{tag}.png"), frame)
    # 复现预处理，导出对比图
    img3 = cv2.resize(frame, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img3, cv2.COLOR_BGR2GRAY)
    eq = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    adp = cv2.adaptiveThreshold(eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 15, 8)
    wm = M._white_text_mask(img3)
    row = np.hstack([cv2.cvtColor(img3, cv2.COLOR_BGR2GRAY), eq, adp, wm])
    cv2.imwrite(os.path.join(out_dir, f"compare_{tag}.png"), row)
    print(f"  [{tag}] 对比图已导出 compare_{tag}.png "
          f"(依次: 灰度 / CLAHE / 自适应二值化 / 白色掩码)")
    if have_engine:
        items = M.read_chat_ocr(frame, engine, (0, 0, 1, 1))
        for it in items:
            print(f"    OCR -> {it['text']}  (conf={it['confidence']:.2f})")
        text = M.ocr_str(items)
        print("    ocr_str 输出:\n    " + text.replace("\n", "\n    "))

print("=" * 60)
print(f"结果: PASS={PASS}  FAIL={FAIL}")
print(f"预处理对比图目录: {out_dir}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
