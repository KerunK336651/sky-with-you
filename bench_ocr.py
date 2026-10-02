# -*- coding: utf-8 -*-
"""
bench_ocr.py — read_chat_ocr 性能基线（只读，不改任何产物）

单 delta：新增的 ("white") 变体是否、以及在多大程度上增加一次聊天识别的耗时。
同合同 A/B：local(use_white=False，6变体=baseline) vs local(use_white=True，7变体=candidate)，
同输入、同进程、交替配对、先 warmup；并与真实 M.read_chat_ocr 对拍输出做语义 oracle。

学校无游戏，workload 用合成的光遇风格聊天图（亮/暗），proven scope 仅限此合成 workload；
真机数字需回家用真实截图复跑本脚本（把 make_chat_image 换成读图即可）。

输出：控制台原始样本+汇总；原始样本写 _ocr_bench/bench_raw.json。
用法:  py bench_ocr.py
"""
import os, sys, json, time, importlib.util, statistics

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import numpy as np
import cv2

spec = importlib.util.spec_from_file_location(
    "skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


def make_chat_image(bright=True):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 760, 420
    img = Image.new("RGB", (W, H)); px = img.load()
    for y in range(H):
        for x in range(0, W, 4):
            c = (215 - x // 40, 225 - y // 30, 235) if bright else (30 + x // 60, 45 + y // 40, 40)
            for k in range(4):
                if x + k < W:
                    px[x + k, y] = c
    dr = ImageDraw.Draw(img, "RGBA")
    if bright:
        dr.ellipse([520, 30, 720, 200], fill=(245, 240, 220, 255))
        dr.ellipse([60, 260, 240, 400], fill=(230, 225, 200, 255))
        dr.ellipse([400, 250, 560, 380], fill=(210, 230, 160, 255))
    else:
        dr.ellipse([520, 40, 700, 200], fill=(30, 70, 90, 255))
    dr.rounded_rectangle([20, 60, 520, 360], radius=18, fill=(20, 22, 30, 120))
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 30)
    for i, t in enumerate(["珂珂-你在哪里呀", "星河-我在云野等你", "珂珂-一起去暴风眼吗"]):
        dr.text((45, 95 + i * 80), t, font=font, fill=(255, 255, 255, 255),
                stroke_width=2, stroke_fill=(55, 55, 65, 255))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def prep(frame):
    """与源码一致的预处理，返回各中间图和各步耗时。"""
    h, w = frame.shape[:2]
    roi = (0, 0, 1, 1)
    x0, y0, x1, y1 = roi
    t = {}
    t0 = time.perf_counter(); img = frame[int(h*y0):int(h*y1), int(w*x0):int(w*x1)]
    img = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    t["resize3x"] = time.perf_counter() - t0
    t0 = time.perf_counter(); gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); t["bgr2gray"] = time.perf_counter()-t0
    t0 = time.perf_counter(); clahe = cv2.createCLAHE(3.0, (8, 8)); gray_eq = clahe.apply(gray); t["clahe"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    k = np.array([[-1,-1,-1],[-1,9,-1],[-1,-1,-1]]); gray_sharp = cv2.filter2D(gray_eq, -1, k)
    t["sharpen"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    adaptive = cv2.adaptiveThreshold(gray_eq,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,15,8)
    t["adaptiveThr"] = time.perf_counter()-t0
    t0 = time.perf_counter(); white = M._white_text_mask(img); t["white_mask"] = time.perf_counter()-t0
    return dict(img=img, gray_eq=gray_eq, gray_sharp=gray_sharp, adaptive=adaptive,
                white=white), t


def local_read(frame, engine, use_white, counter=None):
    """与 sky-loop-v7.read_chat_ocr 逐行等价、仅参数化 white 变体。返回(items, 推理次数)。"""
    P, _ = prep(frame)
    variants = [("gray", P["gray_eq"]), ("sharp", P["gray_sharp"])]
    if use_white:
        variants.append(("white", P["white"]))
    variants += [("color", P["img"]),
                 ("th150", cv2.threshold(P["gray_eq"],150,255,cv2.THRESH_BINARY)[1]),
                 ("th180", cv2.threshold(P["gray_eq"],180,255,cv2.THRESH_BINARY)[1]),
                 ("adaptive", P["adaptive"])]
    best, best_score, n_infer = [], -1, 0
    for name, im in variants:
        n_infer += 1
        if counter is not None:
            counter[name] = counter.get(name, 0) + 1
        result, _ = engine(im); result = result or []
        if result:
            ac = sum(float(r[2]) for r in result)/len(result)
            score = len(result) + ac
        else:
            ac = 0; score = 0
        if score > best_score:
            best_score, best = score, result
        if name in ("gray","sharp") and len(result) >= 3 and ac > 0.60:
            break
    items = [{"text": str(r[1]), "confidence": float(r[2])} for r in best]
    return items, n_infer


def med(xs): return statistics.median(xs)
def p90(xs):
    s = sorted(xs); k = int(round(0.9*(len(s)-1))); return s[k]


def main():
    outdir = os.path.join(ROOT, "_ocr_bench"); os.makedirs(outdir, exist_ok=True)
    engine = M.make_ocr_engine()
    raw = {"preprocess_ms": {}, "per_variant_infer_ms": {}, "end2end_ms": {}, "n_infer": {}, "oracle": {}}
    N_PREP, N_INF, N_E2E = 60, 15, 12

    for tag, bright in (("bright", True), ("dark", False)):
        frame = make_chat_image(bright)
        P, _ = prep(frame)

        # ── A. 预处理算子耗时（ms）──
        ops = {
            "resize3x": lambda: cv2.resize(frame[int(frame.shape[0]*0):int(frame.shape[0]*1),
                                                 int(frame.shape[1]*0):int(frame.shape[1]*1)],
                                          None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
            "white_mask": lambda: M._white_text_mask(P["img"]),
            "adaptiveThr": lambda: cv2.adaptiveThreshold(P["gray_eq"],255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,15,8),
        }
        raw["preprocess_ms"][tag] = {}
        for name, fn in ops.items():
            for _ in range(5): fn()
            xs = []
            for _ in range(N_PREP):
                t0=time.perf_counter(); fn(); xs.append((time.perf_counter()-t0)*1000)
            raw["preprocess_ms"][tag][name] = dict(med=round(med(xs),3), p90=round(p90(xs),3), max=round(max(xs),3))

        # ── B. 每个变体单次 OCR 推理耗时（ms）──
        vmap = {"gray":P["gray_eq"],"sharp":P["gray_sharp"],"white":P["white"],
                "th150":cv2.threshold(P["gray_eq"],150,255,cv2.THRESH_BINARY)[1],
                "adaptive":P["adaptive"]}
        raw["per_variant_infer_ms"][tag] = {}
        for name, im in vmap.items():
            for _ in range(3): engine(im)
            xs=[]
            for _ in range(N_INF):
                t0=time.perf_counter(); engine(im); xs.append((time.perf_counter()-t0)*1000)
            raw["per_variant_infer_ms"][tag][name]=dict(med=round(med(xs),2),p90=round(p90(xs),2),max=round(max(xs),2))

        # ── C. 端到端 A/B（交替配对，抵消漂移）──
        for u in (False, True):
            for _ in range(3): local_read(frame, engine, u)
        ab = {False: [], True: []}; nc = {False: {}, True: {}}
        for i in range(N_E2E):
            for u in ((i % 2 == 0, i % 2 == 1)):  # 交替
                t0=time.perf_counter(); items, ni = local_read(frame, engine, u, nc[u]); ab[u].append((time.perf_counter()-t0)*1000)
        raw["end2end_ms"][tag] = {
            "baseline_6var": dict(med=round(med(ab[False]),2),p90=round(p90(ab[False]),2),max=round(max(ab[False]),2)),
            "candidate_7var":dict(med=round(med(ab[True]),2), p90=round(p90(ab[True]),2), max=round(max(ab[True]),2)),
        }
        raw["n_infer"][tag] = {"baseline": nc[False], "candidate": nc[True]}

        # ── D. 语义 oracle：真实函数 vs local；white 是否改变最终文本 ──
        real = [x["text"] for x in M.read_chat_ocr(frame, engine, (0,0,1,1))]
        loc_t = [x["text"] for x in local_read(frame, engine, True)[0]]
        loc_f = [x["text"] for x in local_read(frame, engine, False)[0]]
        raw["oracle"][tag] = {"real_eq_local7": real == loc_t,
                              "text6": loc_f, "text7": loc_t,
                              "white_changes_result": loc_f != loc_t}

    # 汇总打印
    print("="*72)
    for tag in ("bright","dark"):
        print(f"[{tag}] 预处理算子耗时 ms (med/p90/max):")
        for k,v in raw["preprocess_ms"][tag].items():
            print(f"   {k:12s} {v['med']:7.3f} / {v['p90']:7.3f} / {v['max']:7.3f}")
        print(f"[{tag}] 单变体一次推理 ms (med/p90/max):")
        for k,v in raw["per_variant_infer_ms"][tag].items():
            print(f"   {k:9s} {v['med']:7.2f} / {v['p90']:7.2f} / {v['max']:7.2f}")
        e=raw["end2end_ms"][tag]
        b,c=e["baseline_6var"]["med"], e["candidate_7var"]["med"]
        print(f"[{tag}] 端到端 median: 6变体 {b:.2f}ms | 7变体 {c:.2f}ms | delta {c-b:+.2f}ms")
        print(f"[{tag}] 推理次数累计: {raw['n_infer'][tag]}")
        o=raw["oracle"][tag]
        print(f"[{tag}] oracle: 真实==local7? {o['real_eq_local7']} | white改变结果? {o['white_changes_result']}")
        print("-"*72)
    with open(os.path.join(outdir,"bench_raw.json"),"w",encoding="utf-8") as f:
        json.dump(raw,f,ensure_ascii=False,indent=2)
    print("raw ->", os.path.join(outdir,"bench_raw.json"))


if __name__ == "__main__":
    main()
