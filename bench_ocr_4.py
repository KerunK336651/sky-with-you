# -*- coding: utf-8 -*-
"""
bench_ocr_4.py — 聊天OCR「7变体精简」实验合同（只读，不砍任何变体）

为什么需要它：read_chat_ocr 对 gray/sharp/white/color/th150/th180/adaptive 七个变体
串行推理，gray/sharp 命中(>=3行且均conf>0.60)才提前退出；难帧会7次全跑。要砍变体，
必须证明被砍变体在「gray救不回来、确实走到后面的难帧」上【没有不可替代的行】——
在全是gray命中的常见帧上砍谁都一样，会有幸存者偏差。

本脚本对每帧只跑一次7变体，然后：
  1) 重放现状序列，得到推理次数/执行序列/最终best；
  2) 用 _msg_similar 把各变体识别行聚成行簇，统计每个变体的【独有行】【gray未覆盖行】；
  3) 重放候选精简序列(A/B/C)，与现状best做行级覆盖oracle（丢了哪些行）；
  4) 分层汇总：常见帧(gray命中) vs 难帧(gray未命中)。难帧=0 时明确输出"证据不足、不授权砍除"。

workload：flip_debug 干净1080p面板开帧(底部提示行相关>=0.6，最多6张)
          + ocr_hard_shots/ 里的任意真机难帧（gray识别不出/要走到后面变体的帧，全用）。
结果写 _ocr_bench/bench4_raw.json。用法: py bench_ocr_4.py
"""
import os, sys, glob, time, json, statistics, importlib.util
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
import numpy as np, cv2
spec = importlib.util.spec_from_file_location("skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
from rapidocr_onnxruntime import RapidOCR

ROI = (0.01, 0.30, 0.40, 0.90)
ORDER = ["gray", "sharp", "white", "color", "th150", "th180", "adaptive"]
PLANS = {
    "现状7变体": ORDER,
    "A砍三值化": ["gray", "sharp", "white", "color"],
    "B再砍sharp": ["gray", "white", "color"],
    "C仅gray+white": ["gray", "white"],
}
HINT = (0.0, 0.950, 0.32, 0.999)

def crop1(frame):
    h, w = frame.shape[:2]; x0,y0,x1,y1 = ROI
    return frame[int(h*y0):int(h*y1), int(w*x0):int(w*x1)]   # fx=1，与续29后线上一致

def hint_roi(fr):
    gh, gw = fr.shape[:2]; gg = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    return gg[int(gh*HINT[1]):int(gh*HINT[3]), int(gw*HINT[0]):int(gw*HINT[2])]

def run_variants(frame, eng):
    """7变体各跑一次，返回 {name:(texts,avg_conf,n_lines,ms)}。"""
    vs = M.build_ocr_variants(crop1(frame))
    out = {}
    for name, im in vs:
        t = time.perf_counter(); r, _ = eng(im); ms = (time.perf_counter()-t)*1000
        r = r or []
        texts = [str(x[1]) for x in r]
        ac = sum(float(x[2]) for x in r)/len(r) if r else 0.0
        out[name] = (texts, ac, len(r), ms)
    return out

def replay(order, res):
    """复刻 read_chat_ocr 的选择+提前退出，返回(执行序列, best文本list, 推理次数)。"""
    best, bs, seq = [], -1, []
    for name in order:
        texts, ac, n, _ = res[name]; seq.append(name)
        sc = n + ac
        if sc > bs: bs, best = sc, list(texts)
        if name in ("gray", "sharp") and n >= 3 and ac > 0.60:
            break
    return seq, best, len(seq)

def cluster_lines(res):
    """把各变体的行用_msg_similar聚簇，返回 clusters=[[代表, {识别到它的变体}]]。"""
    clusters = []
    for name in ORDER:
        for line in res[name][0]:
            hit = -1
            for i, (rep, owners) in enumerate(clusters):
                if M._msg_similar(line, rep): hit = i; break
            if hit < 0: clusters.append([line, {name}])
            else: clusters[hit][1].add(name)
    return clusters

def cover_ratio(ref_best, cand_best):
    """现状best的每行是否被候选best宽松覆盖，返回(覆盖率, 丢失行)。"""
    if not ref_best: return 1.0, []
    lost = [ln for ln in ref_best
            if not any(M._msg_similar(ln, c) for c in cand_best)]
    return 1 - len(lost)/len(ref_best), lost

def select_frames(eng):
    os.makedirs(os.path.join(ROOT, "ocr_hard_shots"), exist_ok=True)
    _tmpl = hint_roi(cv2.imread(os.path.join(ROOT,"flip_debug","chat_180001_on_b100_t3.jpg")))
    def hscore(fr):
        r = hint_roi(fr)
        if r.shape != _tmpl.shape: r = cv2.resize(r, (_tmpl.shape[1], _tmpl.shape[0]))
        return float(cv2.matchTemplate(r, _tmpl, cv2.TM_CCOEFF_NORMED)[0,0])
    clean = []
    for p in sorted(glob.glob(os.path.join(ROOT,"flip_debug","chat_*.jpg"))):
        f = cv2.imread(p)
        if f is None or f.shape[1] < 1500 or hscore(f) < 0.60: continue
        g = cv2.cvtColor(crop1(f), cv2.COLOR_BGR2GRAY)
        n = len((eng(g) or []))  # 粗估行数用于挑有字帧
        clean.append((n, p))
    clean.sort(reverse=True)
    frames = [("clean:"+os.path.basename(p), cv2.imread(p)) for _,p in clean[:6]]
    for p in sorted(glob.glob(os.path.join(ROOT,"ocr_hard_shots","*.*"))):
        if p.lower().endswith((".jpg",".jpeg",".png",".bmp")):
            f = cv2.imread(p)
            if f is not None: frames.append(("HARD:"+os.path.basename(p), f))
    return frames

def main():
    outdir = os.path.join(ROOT,"_ocr_bench"); os.makedirs(outdir, exist_ok=True)
    eng = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    frames = select_frames(eng)
    print("workload:", [n for n,_ in frames] or "空")
    agg = {"per_variant_picked": {k:0 for k in ORDER},
           "per_variant_unique": {k:0 for k in ORDER},
           "per_variant_unique_on_hard": {k:0 for k in ORDER},
           "per_variant_ms": {k:[] for k in ORDER}}
    plan_infer = {k:0 for k in PLANS}; plan_lost_frames = {k:[] for k in PLANS}
    cur_infer = 0; n_hard = 0; per_frame = []

    for name, f in frames:
        res = run_variants(f, eng)
        for k in ORDER: agg["per_variant_ms"][k].append(round(res[k][3]))
        seq, best, ni = replay(ORDER, res)
        cur_infer += ni
        hard = not ("gray" in seq and len(seq) == 1)     # gray没单独命中=难帧
        if hard: n_hard += 1
        clusters = cluster_lines(res)
        for rep, owners in clusters:
            if len(owners) == 1:
                v = next(iter(owners)); agg["per_variant_unique"][v] += 1
                if hard: agg["per_variant_unique_on_hard"][v] += 1
        # 现状最终best来自哪个变体（score最高者）
        pick = max(ORDER, key=lambda k: res[k][2] + res[k][1])
        agg["per_variant_picked"][pick] += 1
        row = {"frame": name, "hard": hard, "cur_seq": seq, "cur_infer": ni}
        for pl, order in PLANS.items():
            if pl == "现状7变体": continue
            _, cbest, cni = replay(order, res)
            plan_infer[pl] += cni
            cov, lost = cover_ratio(best, cbest)
            if lost: plan_lost_frames[pl].append({"frame":name,"lost":lost})
            row[pl] = {"infer":cni, "cover":round(cov,3), "lost":lost}
        plan_infer["现状7变体"] += ni
        per_frame.append(row)
        print(f"\n[{name}] 难帧={hard} 现状序列={seq}({ni}次)")
        for pl in PLANS:
            if pl=="现状7变体": continue
            r=row[pl]; print(f"   {pl:12} {r['infer']}次 覆盖={r['cover']} 丢行={r['lost']}")

    print("\n"+"="*72)
    print(f"总帧={len(frames)}  其中gray未命中难帧={n_hard}")
    print("各变体被选为best次数:", agg["per_variant_picked"])
    print("各变体独有行(全部帧):", agg["per_variant_unique"])
    print("各变体独有行(仅难帧):", agg["per_variant_unique_on_hard"])
    print("各变体单次ms中位:", {k: round(statistics.median(v)) if v else None
                                for k,v in agg["per_variant_ms"].items()})
    print("推理次数合计:", plan_infer)
    for pl in PLANS:
        if pl=="现状7变体": continue
        lf = plan_lost_frames[pl]
        verdict = "可考虑(难帧零丢行)" if (n_hard>0 and not lf) else \
                  ("证据不足:当前无难帧，不授权砍除" if n_hard==0 else f"会丢行，禁止砍:{len(lf)}帧")
        print(f"  {pl:12} 合计推理={plan_infer[pl]:3}(现状{plan_infer['现状7变体']:3})  {verdict}")
    if n_hard == 0:
        print("\n[结论] 现有帧 gray 全部一次命中，无法评估变体精简；请把真机上 gray 识别不出、")
        print("       日志里走到 sharp/white/... 的帧存到 ocr_hard_shots/ 后重跑本脚本。")

    agg.update(plan_infer=plan_infer, plan_lost_frames=plan_lost_frames,
               n_frames=len(frames), n_hard=n_hard, per_frame=per_frame)
    with open(os.path.join(outdir,"bench4_raw.json"),"w",encoding="utf-8") as fp:
        json.dump(agg, fp, ensure_ascii=False, indent=2)
    print("raw ->", os.path.join(outdir,"bench4_raw.json"))

if __name__ == "__main__":
    main()
