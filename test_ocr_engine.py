# -*- coding: utf-8 -*-
"""test_ocr_engine.py — OCR 引擎装配的离线自检（走真 make_ocr_engine()，不连游戏、不联网）。

为什么要它：panel_detector 新增了 SKY_OCR_DEVICE / SKY_OCR_MODEL 两个开关和三条回退路径
（模型缺失→v3、v5 创建失败→v3、无 DirectML→CPU），这些分支此前没有任何断言保护——
一旦悄悄退回 v3 或退回 CPU，程序照样能跑，只有速度和识别质量变差，肉眼很难发现。

判定手段是「模型指纹」：看 rec 模型输出的类别数，而不是看日志怎么说。
    v3（RapidOCR 自带 PP-OCRv3）  = 6625
    v5（models/ocr 的 PP-OCRv5）  = 18385

顺序说明：DirectML 补丁一旦打上就在本进程内永久生效（与真实运行一致，因为 OCR_DEVICE
只在 import 时读一次）。所以**必须先测"强制 CPU"，再测 auto/dml**，否则测不出 CPU 分支。

运行：py test_ocr_engine.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import panel_detector as PD

V3_CLASSES = 6625      # PP-OCRv3 rec 类别数（实测指纹）
V5_CLASSES = 18385     # PP-OCRv5 rec 类别数（实测指纹）

npass = nfail = nskip = 0


def check(desc, ok, extra=""):
    global npass, nfail
    if ok:
        npass += 1
        print("  [PASS] %s" % desc)
    else:
        nfail += 1
        print("  [FAIL] %s   %s" % (desc, extra))


def skip(desc, why):
    global nskip
    nskip += 1
    print("  [SKIP] %s   （%s）" % (desc, why))


def rec_classes(eng):
    """模型指纹：rec 输出最后一维 = 类别数（含 blank/空格）。"""
    return eng.text_recognizer.session.session.get_outputs()[0].shape[-1]


def det_provider(eng):
    return eng.text_detector.infer.session.get_providers()[0]


def rec_provider(eng):
    return eng.text_recognizer.session.session.get_providers()[0]


def can_run(eng):
    """真跑一次推理（合成一张深底白字图），确认引擎不只是"建得起来"而是"跑得动"。"""
    img = np.full((120, 420, 3), 30, dtype=np.uint8)
    try:
        res, _ = eng(img)
        return True, "返回 %s" % ("None" if res is None else "%d 行" % len(res))
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:80])


def has_dml():
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


print("环境: OCR_MODEL=%s OCR_DEVICE=%s DML可用=%s"
      % (PD.OCR_MODEL, PD.OCR_DEVICE, has_dml()))
print()

# ---------- 0) 必需依赖 ----------
print("0) 依赖")
check("RapidOCR 可用（requirements 里的必需依赖）", PD.RapidOCR is not None,
      "rapidocr_onnxruntime 未安装")
if PD.RapidOCR is None:
    print("\nPASS=%d FAIL=%d SKIP=%d" % (npass, nfail, nskip))
    sys.exit(1)

V5_READY = all(os.path.exists(p) for p in PD._OCR_V5.values())
print("  v5 模型文件齐全: %s" % V5_READY)

# ---------- 1) 强制 CPU（必须在任何 auto/dml 调用之前） ----------
print("\n1) SKY_OCR_DEVICE=cpu 强制走 CPU")
PD.OCR_DEVICE = "cpu"
PD.OCR_MODEL = "v3"
e_cpu = PD.make_ocr_engine()
check("强制 CPU 时确实拿到 CPU provider",
      det_provider(e_cpu) == "CPUExecutionProvider"
      and rec_provider(e_cpu) == "CPUExecutionProvider",
      "实际 det=%s rec=%s" % (det_provider(e_cpu), rec_provider(e_cpu)))
check("默认模型是 v3（指纹 %d）" % V3_CLASSES, rec_classes(e_cpu) == V3_CLASSES,
      "实际 %d" % rec_classes(e_cpu))
ok, info = can_run(e_cpu)
check("v3/CPU 引擎能真跑一次推理", ok, info)

# ---------- 2) v5 模型 + 字典管线 ----------
print("\n2) SKY_OCR_MODEL=v5（仍是 CPU，验证模型与字典）")
if not V5_READY:
    skip("v5 引擎指纹", "models/ocr 下缺文件")
else:
    PD.OCR_MODEL = "v5"
    e5 = PD.make_ocr_engine()
    check("v5 真的加载了（指纹 %d，而不是静默回退到 %d）" % (V5_CLASSES, V3_CLASSES),
          rec_classes(e5) == V5_CLASSES, "实际 %d" % rec_classes(e5))
    chars = getattr(e5.text_recognizer.postprocess_op, "character", None)
    check("v5 解码器字典长度 = 模型类别数（keys 管线通了）",
          chars is not None and len(chars) == V5_CLASSES,
          "实际 %s" % (len(chars) if chars is not None else None))
    check("v5 字典头部是 blank + 全角空格（PP-OCR 标准结构）",
          chars is not None and list(chars[:2]) == ["blank", "\u3000"],
          "实际 %r" % (list(chars[:2]) if chars else None))
    ok, info = can_run(e5)
    check("v5 引擎能真跑一次推理（字典错会在这里炸）", ok, info)

# ---------- 3) 模型缺失 -> 回退 v3 ----------
print("\n3) SKY_OCR_MODEL=v5 但模型文件缺失 -> 回退 v3")
_saved = dict(PD._OCR_V5)
try:
    PD._OCR_V5 = {k: os.path.join(HERE, "models", "ocr", "_not_exist_%s.onnx" % k)
                  for k in _saved}
    PD.OCR_MODEL = "v5"
    e_fb = PD.make_ocr_engine()
    check("模型缺失时回退到 v3（指纹 %d），不抛异常" % V3_CLASSES,
          e_fb is not None and rec_classes(e_fb) == V3_CLASSES,
          "实际 %s" % (rec_classes(e_fb) if e_fb else None))
finally:
    PD._OCR_V5 = _saved

# ---------- 4) 切到 auto/dml：DirectML 补丁 ----------
print("\n4) SKY_OCR_DEVICE=auto（有 DML 就该走 GPU）")
PD.OCR_MODEL = "v3"
PD.OCR_DEVICE = "auto"
e_auto = PD.make_ocr_engine()
if has_dml():
    check("有 DirectML 时 det/rec 都拿到 DmlExecutionProvider",
          det_provider(e_auto) == "DmlExecutionProvider"
          and rec_provider(e_auto) == "DmlExecutionProvider",
          "实际 det=%s rec=%s" % (det_provider(e_auto), rec_provider(e_auto)))
    ok, info = can_run(e_auto)
    check("v3/DML 引擎能真跑一次推理", ok, info)
else:
    skip("DML provider 断言", "本机 onnxruntime 无 DmlExecutionProvider")
    check("无 DML 时自动回退 CPU，不抛异常",
          det_provider(e_auto) == "CPUExecutionProvider")

print("\n5) 补丁幂等 / 挂机安全")
import rapidocr_onnxruntime.utils as _u
_p1 = _u.InferenceSession
PD._patch_rapidocr_dml()
PD._patch_rapidocr_dml()
check("重复调用 _patch_rapidocr_dml() 不会层层包装 session", _u.InferenceSession is _p1,
      "InferenceSession 被反复替换")
if has_dml():
    check("有 DML 时补丁标记已置位（幂等靠它）",
          getattr(_p1, "_sky_dml_patched", False) is True)
else:
    skip("补丁标记断言", "本机无 DML，补丁本就不该打（此时 InferenceSession 应为原生函数）")
    check("无 DML 时不打补丁（保持原生 InferenceSession）",
          not getattr(_p1, "_sky_dml_patched", False))
ok = True
try:
    e2 = PD.make_ocr_engine()          # 再建一次引擎（模拟第二个 OCR 线程）
except Exception as e:
    ok = False
check("连续创建多个引擎（项目里 3 个调用点）不抛异常", ok)

print("\n--- ocr_infer：推理串行化（2026-10-06 DML 崩溃后的加固）---")

import threading as _threading
import time as _time

# 背景：真机崩在 amdxc64.dll，调用链 ORT->DirectML->D3D12（无 d3d11/dxgi 帧），
# 而循环里有 3 个引擎 / 6 个 DirectML 会话 / 3 个线程并发推理。改成串行化。
check("默认开启串行化（SKY_OCR_SERIALIZE 未设时为 1）",
      PD._OCR_SERIALIZE is True)
check("锁是真实存在的 Lock 对象",
      hasattr(PD._OCR_INFER_LOCK, "acquire") and hasattr(PD._OCR_INFER_LOCK, "release"))

check("engine 为 None 时返回 (None, None)，不抛异常",
      PD.ocr_infer(None, object()) == (None, None))


class _FakeEngine(object):
    """假引擎：记录同时进入的次数，用来验证串行化。"""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.inside = 0
        self.max_inside = 0
        self.calls = 0
        import threading as _t
        self._guard = _t.Lock()

    def __call__(self, img):
        with self._guard:
            self.inside += 1
            self.calls += 1
            self.max_inside = max(self.max_inside, self.inside)
        _time.sleep(self.delay)
        with self._guard:
            self.inside -= 1
        return [("box", "text", 0.9)], [1.0]


import threading as _threading

eng = _FakeEngine()
try:
    r, el = PD.ocr_infer(eng, "img")
    ok = (r == [("box", "text", 0.9)] and el == [1.0] and eng.calls == 1)
except Exception:
    ok = False
check("正常调用：透传引擎结果，且只调用一次", ok)

# 拉 4 个线程同时打同一个引擎：串行化下 max_inside 必须为 1
eng2 = _FakeEngine(delay=0.08)
threads = [_threading.Thread(target=PD.ocr_infer, args=(eng2, "img")) for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("并发 4 线程 -> 同时进入引擎的最多 1 个（已串行化）",
      eng2.max_inside == 1 and eng2.calls == 4)

# 关掉串行化应当能观察到并发（证明开关真的起作用，而不是恒等于 1）
real_flag = PD._OCR_SERIALIZE
try:
    PD._OCR_SERIALIZE = False
    eng3 = _FakeEngine(delay=0.12)
    threads = [_threading.Thread(target=PD.ocr_infer, args=(eng3, "img")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("SKY_OCR_SERIALIZE=0（关闭）-> 能观察到并发（开关有效）",
          eng3.max_inside >= 2 and eng3.calls == 4)
finally:
    PD._OCR_SERIALIZE = real_flag
check("测试后恢复默认开关", PD._OCR_SERIALIZE is True)

# 结构断言：三处推理调用点都必须走 ocr_infer，别再有裸调用
_root = os.path.dirname(os.path.abspath(__file__))
_pd_src = open(os.path.join(_root, "panel_detector.py"), "rb").read().decode("utf-8")
_loop_src = open(os.path.join(_root, "sky-loop-v7.py"), "rb").read().decode("utf-8")
check("panel_detector 里没有裸的 self._ensure_ocr_engine()(img)",
      "_ensure_ocr_engine()(img)" not in _pd_src)
check("sky-loop 里没有裸的 ocr_engine(im)", "ocr_engine(im)" not in _loop_src)
check("sky-loop 里没有裸的 engine(gray_eq)", "engine(gray_eq)" not in _loop_src)
check("三处调用点都用 ocr_infer",
      _pd_src.count("ocr_infer(") >= 2 and _loop_src.count("ocr_infer(") >= 2)

print("\nPASS=%d FAIL=%d SKIP=%d" % (npass, nfail, nskip))
if nfail:
    print("有 %d 条失败" % nfail)
    sys.exit(1)
print("全部通过")
