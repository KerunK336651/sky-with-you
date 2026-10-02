# -*- coding: utf-8 -*-
"""
bench_ocr_3.py — OCR 慢的单 delta 诊断（只读，不改主链路）
坐实三个候选杠杆（同输入、同进程、warmup、取中位）：
  Delta1 放大倍数 fx {1,2,3}：现状 crop_roi_3x 放大3倍是否白花钱
  Delta2 det 限制边长 det_limit_side_len {480,640,960,默认}
  Delta3 7个变体逐个的单次耗时/行数（找冗余 + 现状退出条件走到第几变体）
  端到端 A/B：现状(fx3+7变体) vs 只改放大(fx1+7变体)，oracle=最终文本不丢
workload = flip_debug 里真实 1080p 帧自动选识别行数 top-N（复杂难帧优先）。
结果写 _ocr_bench/bench3_raw.json。用法: py bench_ocr_3.py
"""
import os, sys, glob, time, json, statistics, importlib.util
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
import numpy as np, cv2
spec = importlib.util.spec_from_file_location("skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
from rapidocr_onnxruntime import RapidOCR

ROI = (0.01, 0.30, 0.40, 0.90)
med = lambda xs: statistics.median(xs)

def mk(**kw):
    base = dict(intra_op_num_threads=2, inter_op_num_threads=1)
    base.update(kw)
    return RapidOCR(**base)

def crop(frame, fx):
    h, w = frame.shape[:2]; x0,y0,x1,y1 = ROI
    c = frame[int(h*y0):int(h*y1), int(w*x0):int(w*x1)]
    return cv2.resize(c, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC) if fx != 1 else c

def variants(frame, fx):
    im = crop(frame, fx)
    return M.build_ocr_variants(im)

def once(eng, im):
    t = time.perf_counter(); r, _ = eng(im); dt = (time.perf_counter()-t)*1000
    r = r or []
    ac = sum(float(x[2]) for x in r)/len(r) if r else 0.0
    txt = "|".join(str(x[1]) for x in r)
    return dt, len(r), ac, txt

def timed(eng, im, rep):
    eng(im)  # warmup
    xs = []
    for _ in range(rep):
        t = time.perf_counter(); eng(im); xs.append((time.perf_counter()-t)*1000)
    return med(xs)

def e2e(frame, eng, fx):
    """复刻 read_chat_ocr：7变体串行 + gray/sharp 提前退出，返回(med ms, 最终文本, 执行序列)"""
    vs = variants(frame, fx)
    best, bs, seq = [], -1, []
    t0 = time.perf_counter()
    for name, im in vs:
        r, _ = eng(im); r = r or []; seq.append(name)
        ac = sum(float(x[2]) for x in r)/len(r) if r else 0
        sc = len(r) + ac
        if sc > bs: bs, best = sc, r
        if name in ("gray","sharp") and len(r) >= 3 and ac > 0.60:
            break
    return (time.perf_counter()-t0)*1000, "|".join(str(x[1]) for x in best), seq

def main():
    outdir = os.path.join(ROOT, "_ocr_bench"); os.makedirs(outdir, exist_ok=True)
    eng = mk()
    # ---- 自动选帧：只挑【干净的游戏面板开帧】（底部提示行模板相关>=0.6，排除控制台盖屏污染帧），再按 fx1 gray 行数 top3 ----
    HINT = (0.0, 0.950, 0.32, 0.999)
    def hint_roi(fr):
        gh, gw = fr.shape[:2]
        gg = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        return gg[int(gh*HINT[1]):int(gh*HINT[3]), int(gw*HINT[0]):int(gw*HINT[2])]
    _tmpl = hint_roi(cv2.imread(os.path.join(ROOT,"flip_debug","chat_180001_on_b100_t3.jpg")))
    def hint_score(fr):
        r = hint_roi(fr)
        if r.shape != _tmpl.shape: r = cv2.resize(r, (_tmpl.shape[1], _tmpl.shape[0]))
        return float(cv2.matchTemplate(r, _tmpl, cv2.TM_CCOEFF_NORMED)[0,0])
    cand = []
    audit = []
    for p in sorted(glob.glob(os.path.join(ROOT,"flip_debug","chat_*.jpg"))):
        f = cv2.imread(p)
        if f is None or f.shape[1] < 1500: continue      # 只要1080p
        hs = hint_score(f)
        _, n, _, _ = once(eng, cv2.cvtColor(crop(f,1), cv2.COLOR_BGR2GRAY))
        audit.append((os.path.basename(p), round(hs,2), n))
        if hs >= 0.60 and n >= 1: cand.append((n, p))    # 干净面板开帧
    cand.sort(reverse=True)
    print("帧审计(name/hint相关/行数):", audit)
    frames = [(os.path.basename(p), cv2.imread(p)) for _, p in cand[:3]]
    print("选中干净 workload:", [(n, c[0]) for c,(n,_) in zip(frames,cand[:3])] or "无有字干净帧！")
    raw = {"frames":[n for n,_ in frames], "audit":audit}
    if not frames:
        print("flip_debug 没有含字的干净1080p面板开帧，无法bench"); return

    # ---- Delta1 放大倍数（gray 变体）----
    print("\n== Delta1 放大倍数 fx（gray, med ms / 行数） ==")
    raw["d1_fx"] = {}
    for name, f in frames:
        row = {}
        for fx in (1,2,3):
            g = cv2.cvtColor(crop(f,fx), cv2.COLOR_BGR2GRAY)
            eq = cv2.createCLAHE(3.0,(8,8)).apply(g)
            ms = timed(eng, eq, 4); _, n, ac, txt = once(eng, eq)
            row[fx] = dict(ms=round(ms), lines=n, conf=round(ac,3), text=txt[:120])
            print(f"  {name[:24]:24} fx={fx} {ms:7.0f}ms 行={n} conf={ac:.2f}")
        raw["d1_fx"][name] = row

    # ---- Delta2 det_limit_side_len（fx=1 gray）----
    print("\n== Delta2 det_limit_side_len（fx=1 gray, med ms / 行数） ==")
    raw["d2_det"] = {}
    engs = {"default": eng}
    for dl in (480, 640, 960):
        try: engs[str(dl)] = mk(det_limit_side_len=dl)
        except Exception as e: print("  引擎不接受 det_limit_side_len:", e); break
    for name, f in frames:
        eq = cv2.createCLAHE(3.0,(8,8)).apply(cv2.cvtColor(crop(f,1),cv2.COLOR_BGR2GRAY))
        row = {}
        for tag, e in engs.items():
            ms = timed(e, eq, 4); _, n, ac, _ = once(e, eq)
            row[tag] = dict(ms=round(ms), lines=n, conf=round(ac,3))
            print(f"  {name[:24]:24} det={tag:7} {ms:7.0f}ms 行={n} conf={ac:.2f}")
        raw["d2_det"][name] = row

    # ---- Delta3 七变体逐个（fx=1 默认引擎）----
    print("\n== Delta3 七变体逐个（fx=1, med ms / 行数 / conf） ==")
    raw["d3_variants"] = {}
    for name, f in frames:
        vs = variants(f, 1); row = {}
        for vn, im in vs:
            ms = timed(eng, im, 3); _, n, ac, _ = once(eng, im)
            row[vn] = dict(ms=round(ms), lines=n, conf=round(ac,3))
            print(f"  {name[:22]:22} {vn:8} {ms:6.0f}ms 行={n} conf={ac:.2f}")
        raw["d3_variants"][name] = row

    # ---- 端到端 A/B：现状 fx3+7var  vs 候选 fx1+7var（单delta=只改放大）----
    print("\n== 端到端 A/B（med ms，3次；oracle 文本） ==")
    raw["d4_e2e"] = {}
    for name, f in frames:
        row = {}
        for tag, fx in (("现状fx3",3),("候选fx1",1)):
            for _ in range(1): e2e(f, eng, fx)  # warm
            xs, last_txt, seq = [], None, None
            for _ in range(3):
                dt, tx, sq = e2e(f, eng, fx); xs.append(dt); last_txt, seq = tx, sq
            row[tag] = dict(ms=round(med(xs)), seq=seq, text=last_txt[:160])
            print(f"  {name[:22]:22} {tag} {med(xs):7.0f}ms 序列={seq}")
        same = row["现状fx3"]["text"] == row["候选fx1"]["text"]
        row["文本完全一致"] = same
        print(f"      文本一致? {same}")
        raw["d4_e2e"][name] = row

    with open(os.path.join(outdir,"bench3_raw.json"),"w",encoding="utf-8") as fp:
        json.dump(raw, fp, ensure_ascii=False, indent=2)
    print("\nraw ->", os.path.join(outdir,"bench3_raw.json"))

if __name__ == "__main__":
    main()
