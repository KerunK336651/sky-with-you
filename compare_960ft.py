# -*- coding: utf-8 -*-
"""多尺寸对比微调模型与当前 v8：val@960 / @1280 / @1920 + 真实截图速度。

用法：
  py compare_960ft.py <新模型.pt>                     # 默认尺寸 960,1280,1920
  py compare_960ft.py <新模型.pt> --sizes 960,1280
"""
import argparse
import glob
import os
import statistics
import time

from ultralytics import YOLO

BASE = os.path.dirname(os.path.abspath(__file__))
CUR = os.path.join(BASE, "models", "yolo", "sky_v8_28cls_best.pt")
DATA = r"D:\sky_yolo_train_v7_28cls\data.yaml"
SHOT_DIR = os.path.join(BASE, "flip_debug")


def val_one(model_path, imgsz):
    m = YOLO(model_path)
    name = "cmp_%s_%d" % (os.path.basename(model_path).replace(".pt", ""), imgsz)
    r = m.val(data=DATA, imgsz=imgsz, split="val", device="cpu",
              project=os.path.join(BASE, "runs", "val_compare"),
              name=name, exist_ok=True, verbose=False, plots=False, cache=False)
    return round(float(r.box.mp), 4), round(float(r.box.mr), 4), \
        round(float(r.box.map50), 4), round(float(r.box.map), 4)


def shot_speed(model_path, imgsz, files):
    m = YOLO(model_path)
    for f in files[:2]:
        m(f, imgsz=imgsz, verbose=False)
    ts = []
    for f in files:
        t = time.perf_counter()
        m(f, imgsz=imgsz, conf=0.5, verbose=False)
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.mean(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new", help="新模型 .pt 路径")
    ap.add_argument("--sizes", default="960,1280,1920")
    args = ap.parse_args()
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    files = sorted(glob.glob(os.path.join(SHOT_DIR, "*.jpg")))[:40]

    print("=" * 72)
    print("对比：%s   vs   当前 v8" % os.path.basename(args.new))
    print("=" * 72)
    table = {}
    for size in sizes:
        for label, path in [("新模型", args.new), ("当前v8", CUR)]:
            print("[%s] val@%d ..." % (label, size), flush=True)
            p, r, m50, m5095 = val_one(path, size)
            table[(size, label)] = (p, r, m50, m5095)
            print("      P %.4f R %.4f mAP50 %.4f mAP50-95 %.4f" % (p, r, m50, m5095), flush=True)

    print("\n%-6s | %-34s | %-34s | 差值(新-旧)" % ("尺寸", "新模型 P/R/mAP50/mAP50-95", "当前 v8 P/R/mAP50/mAP50-95"))
    print("-" * 100)
    for size in sizes:
        new = table[(size, "新模型")]
        cur = table[(size, "当前v8")]
        print("%-6d | %6.4f %6.4f %6.4f %6.4f | %6.4f %6.4f %6.4f %6.4f | %+0.4f"
              % (size, *new, *cur, new[3] - cur[3]))

    print("\n真实截图（%d 张）速度：" % len(files))
    for label, path in [("新模型", args.new), ("当前v8", CUR)]:
        print("  %-5s @960: %.0f ms/张" % (label, shot_speed(path, 960, files)))

    # 结论以部署尺寸 960 为准
    new960 = table[(960, "新模型")][3]
    cur960 = table[(960, "当前v8")][3]
    print("\n部署尺寸 @960 对比：新 %.4f vs 当前 %.4f" % (new960, cur960))
    if new960 > cur960 + 0.003:
        print("结论：新模型 @960 明显更好，建议切换（记得再把 @1280 看一眼）。")
    elif new960 < cur960 - 0.003:
        print("结论：新模型 @960 更差，保持当前 v8。")
    else:
        print("结论：@960 基本持平（<0.003），保持当前 v8；1920 的收益只体现在 1920 推理上，部署用不上。")


if __name__ == "__main__":
    main()
