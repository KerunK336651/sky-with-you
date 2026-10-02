# -*- coding: utf-8 -*-
"""离线评测聊天面板各候选判据在 开(on)/关(off) 历史帧上的分离度。只读，不改任何运行逻辑。
特征：
  band   现有"满宽暗横条"强度（_detect_chat, 越大越像开）
  wod    消息区暗底白字佐证
  bright 条带内亮点
  pencil 现有铅笔图标模板匹配分(0~1)
  hint_w 底部按键提示行(ESC退后/ENTER聊天/T语音输入)浅色像素占比 —— 候选新主锚点
  hint_e 底部提示行 Canny 边缘密度
"""
import os, re, glob, io
import numpy as np, cv2
from panel_detector import PanelDetector, _crop

ROOT = os.path.dirname(os.path.abspath(__file__))
FLIP = os.path.join(ROOT, "flip_debug")
HINT_ROI = (0.0, 0.955, 0.30, 0.998)   # 底部按键提示行搜索区

det = PanelDetector()
cfg = det.cfg
pat = re.compile(r"chat_\d+_(on|off)_b(-?\d+)_t(-?\d+)\.jpg")

def feats(path):
    frame = cv2.imread(path, cv2.IMREAD_COLOR)
    if frame is None: return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]
    raw, conf, m = det._detect_chat(frame, gray)
    pencil = det.templates.match(_crop(gray, cfg.roi_chat_box), "chat", h) if det.templates.has("chat") else -1
    hint = _crop(gray, HINT_ROI)
    hint_w = float(np.mean(hint > 195))
    hint_e = float(np.mean(cv2.Canny(hint, 50, 120) > 0))
    return dict(band=m.get("chat_band", -1), wod=m.get("chat_wod", -1),
                bright=m.get("chat_bright", -1), pencil=pencil,
                hint_w=hint_w, hint_e=hint_e, raw=raw, size=gray.shape[1::-1])

groups = {"on": [], "off": []}
rows = []
for p in sorted(glob.glob(os.path.join(FLIP, "chat_*.jpg"))):
    mm = pat.search(os.path.basename(p))
    if not mm: continue
    tag = mm.group(1)
    f = feats(p)
    if f is None: continue
    f["name"] = os.path.basename(p); f["tag"] = tag
    groups[tag].append(f); rows.append(f)

KEYS = ["band", "wod", "bright", "pencil", "hint_w", "hint_e"]

def stat(vals):
    a = np.array(vals, dtype=float)
    return f"n={len(a)} min={a.min():.3f} max={a.max():.3f} mean={a.mean():.3f}"

lines = []
def out(s=""):
    print(s); lines.append(s)

out("===== 样本尺寸（第一张） =====")
out(str(rows[0]["size"]) if rows else "无样本")
for k in KEYS:
    out(f"\n===== 特征 {k} =====")
    ov = [r[k] for r in groups["on"]]
    fv = [r[k] for r in groups["off"]]
    out("  ON  " + stat(ov))
    out("  OFF " + stat(fv))
    # 期望 on 整体大于 off：on最小值 - off最大值 = 可分间隔（>0 即存在完美阈值）
    if ov and fv:
        gap = min(ov) - max(fv)
        out(f"  >> 可分间隔 on_min - off_max = {gap:+.3f}  ({'存在干净阈值' if gap>0 else '有重叠，不能单阈值分开'})")

out("\n===== 逐帧明细（重点看 off 高 band 的坏样本） =====")
hdr = f"{'tag':3} {'band':>5} {'wod':>6} {'bright':>6} {'pencil':>6} {'hint_w':>7} {'hint_e':>7}  name"
out(hdr)
for r in sorted(rows, key=lambda x:(x["tag"],x["name"])):
    out(f"{r['tag']:3} {r['band']:5.2f} {r['wod']:6.3f} {r['bright']:6.3f} "
        f"{r['pencil']:6.2f} {r['hint_w']:7.4f} {r['hint_e']:7.4f}  {r['name']}")

with io.open(os.path.join(ROOT,"anchor_eval_result.txt"),"w",encoding="utf-8") as fp:
    fp.write("\n".join(lines))
print("\n已写 anchor_eval_result.txt")
