# -*- coding: utf-8 -*-
"""
eval_ocr.py — 真实聊天截图 OCR 批量评测 / 阈值标定工具（离线，不需要游戏实时运行）

【回家怎么用】
  1. 在光遇里打开聊天面板，按地图截一批全屏图（云野亮底/雨林暗底/霞谷黄昏各几张），
     放到  ocr_real_shots/  目录（png/jpg 都行）。
  2. （可选）在该目录建 gt.txt，每行标注正确文本，用于算字准率：
         云野1.png|珂珂-你在干嘛
         雨林1.png|珂珂-一起跑图吗\\n阿颜-等我
     （多行用 \\n；不写 gt 也能看各变体识别结果和耗时）
  3. 运行：  py eval_ocr.py
     报告会打印 + 写到 ocr_real_shots/eval_report.md。
  重点看：哪些图 gray/sharp 翻车、被 white 救回；据此用环境变量
     SKY_WHITE_VMIN / SKY_WHITE_SMAX 调白掩码阈值（不用改代码），再跑对比。

【在学校验证脚本本身】  py eval_ocr.py --selftest  （用合成图跑通整条管线）
"""
import os
import sys
import time
import argparse
import importlib.util
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)

import cv2
import numpy as np

VARIANT_NAMES = ["gray", "sharp", "white", "color", "th150", "th180", "adaptive"]


def make_synth(path, bg, lines):
    """合成一张光遇风格聊天图（仅用于 --selftest 验证管线，非真实字体）。"""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # 半透明聊天底
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([60, 300, 560, 700], fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("msyh.ttc", 30)
    except Exception:
        font = ImageFont.load_default()
    y = 340
    for name, text in lines:
        s = f"{text}-{name}"
        d.text((90, y), s, font=font, fill=(255, 255, 255),
               stroke_width=2, stroke_fill=(40, 40, 40))
        y += 70
    img.save(path)


def run_variant(engine, im):
    t0 = time.time()
    res, _ = engine(im)
    dt = time.time() - t0
    res = res or []
    if not res:
        return 0, 0.0, "", dt
    conf = sum(float(r[2]) for r in res) / len(res)
    txt = "\n".join(M.fix_vocab(M.fix(str(r[1]).strip())) for r in res)
    return len(res), conf, txt, dt


def sim(a, b):
    return SequenceMatcher(None, a.replace("\n", ""), b.replace("\n", "")).ratio()


def load_gt(d):
    gt = {}
    f = os.path.join(d, "gt.txt")
    if os.path.exists(f):
        for line in open(f, encoding="utf-8"):
            line = line.rstrip("\n")
            if "|" in line:
                name, txt = line.split("|", 1)
                gt[name.strip()] = txt.replace("\\n", "\n").strip()
    return gt


def evaluate(d, roi):
    from panel_detector import make_ocr_engine
    engine = make_ocr_engine()
    gt = load_gt(d)
    files = sorted(f for f in os.listdir(d)
                   if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")))
    if not files:
        print("目录里没有图片：", d); return
    # 每变体累计：耗时、conf、对gt相似度、被主链路选中次数
    agg = {v: {"n": 0, "ms": [], "conf": [], "sim": []} for v in VARIANT_NAMES}
    per_image = []
    for fn in files:
        frame = cv2.imdecode(np.fromfile(os.path.join(d, fn), dtype=np.uint8),
                             cv2.IMREAD_COLOR)
        if frame is None:
            print("  读不了:", fn); continue
        roi_img = M.crop_roi_3x(frame, roi)
        variants = M.build_ocr_variants(roi_img)
        rows = {}
        for name, im in variants:
            n, conf, txt, dt = run_variant(engine, im)
            rows[name] = (n, conf, txt, dt)
            a = agg[name]; a["n"] += 1; a["ms"].append(dt * 1000)
            a["conf"].append(conf)
            if fn in gt:
                a["sim"].append(sim(txt, gt[fn]))
        # 线上真实选择（含 gray/sharp 提前 break）
        online = M.read_chat_ocr(frame, engine, roi)
        online_txt = "\n".join(M.fix_vocab(M.fix(x["text"].strip())) for x in online)
        online_sim = sim(online_txt, gt[fn]) if fn in gt else None
        # 诊断最优：对 gt 相似度最高 / 无 gt 时按行数+conf
        if fn in gt:
            best_diag = max(VARIANT_NAMES, key=lambda v: sim(rows[v][2], gt[fn]))
        else:
            best_diag = max(VARIANT_NAMES, key=lambda v: rows[v][1] + rows[v][0])
        per_image.append((fn, rows, online_txt, online_sim, best_diag, gt.get(fn)))
        print(f"\n### {fn}" + (f"  期望={gt[fn]!r}" if fn in gt else ""))
        for v in VARIANT_NAMES:
            n, conf, txt, dt = rows[v]
            mark = " <=诊断最优" if v == best_diag else ""
            print(f"   {v:8s} 行{n} conf={conf:.2f} {dt*1000:6.0f}ms {txt!r}{mark}")
        print(f"   线上主链路最终 -> {online_txt!r}"
              + (f"  字准={online_sim:.2f}" if online_sim is not None else ""))

    # 汇总
    lines = ["# OCR 评测报告", "", f"目录: {d}", f"图片数: {len(per_image)}", "",
             "| 变体 | 平均conf | 中位耗时ms | 对GT平均字准 |",
             "|---|---|---|---|"]
    print("\n" + "=" * 64 + "\n汇总（各变体）\n" + "=" * 64)
    for v in VARIANT_NAMES:
        a = agg[v]
        if not a["n"]:
            continue
        conf = sum(a["conf"]) / len(a["conf"])
        ms = sorted(a["ms"])[len(a["ms"]) // 2]
        s = sum(a["sim"]) / len(a["sim"]) if a["sim"] else float("nan")
        print(f"  {v:8s} conf={conf:.3f}  中位{ms:6.0f}ms  字准={s:.3f}")
        lines.append(f"| {v} | {conf:.3f} | {ms:.0f} | "
                     + (f"{s:.3f}" if a['sim'] else "无GT") + " |")
    # white 救场提示
    saved = [fn for fn, rows, _, _, bd, _ in per_image
             if bd == "white" and sim(rows["white"][2], rows["gray"][2]) > 0.05]
    if saved:
        lines += ["", "## white 变体明显优于 gray 的图（白掩码救场）"] + [f"- {x}" for x in saved]
        print("\nwhite 救场的图:", saved)
    out = os.path.join(d, "eval_report.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    print("\n报告已写:", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default=os.path.join(ROOT, "ocr_real_shots"))
    ap.add_argument("--roi", default="0.01,0.30,0.40,0.90",
                    help="x0,y0,x1,y1 比例，默认=panel_detector 的 roi_chat；裁好的小图用 0,0,1,1")
    ap.add_argument("--selftest", action="store_true", help="用合成图验证管线")
    args = ap.parse_args()
    roi = tuple(float(x) for x in args.roi.split(","))
    d = args.dir
    if args.selftest:
        d = os.path.join(ROOT, "_eval_selftest")
        os.makedirs(d, exist_ok=True)
        make_synth(os.path.join(d, "dark.png"), (30, 34, 60),
                   [("珂珂", "你在干嘛"), ("星河", "在跑图"), ("珂珂", "等等我")])
        make_synth(os.path.join(d, "bright.png"), (150, 190, 210),
                   [("珂珂", "云野好亮"), ("星河", "是啊")])
        open(os.path.join(d, "gt.txt"), "w", encoding="utf-8").write(
            "dark.png|你在干嘛-珂珂\\n在跑图-星河\\n等等我-珂珂\n"
            "bright.png|云野好亮-珂珂\\n是啊-星河\n")
        print("[selftest] 合成图已生成到", d)
    evaluate(d, roi)


if __name__ == "__main__":
    main()
