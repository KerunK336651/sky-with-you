# -*- coding: utf-8 -*-
"""离线测试姿势校验 _verify_pose_with_vision（2026-10-05 二十章重写后的逻辑）。

mock 视觉、mock 按键、把 time.sleep 换成空操作 —— 不打真实 API、不按键、不等待。
测的是 sky-loop-v7.py 里的**真函数**。

覆盖：
  · 两次观察一致且到目标 -> 成功，不补按
  · 两次不一致 / 有一次 sure=false -> 回退盲信，不补按
  · 两次一致但没到目标 -> 补按 1 次 3
  · 补按后距离没变小 -> 停止（防绕圈站起）
  · 补按后观察不一致 -> 停止
  · 补按次数不超过 max_round
  · 视觉返回非法姿态名（防御性，真 classify_pose 不会给）-> 不 KeyError
  · 拿不到帧 / 视觉不可用 -> 回退盲信，不补按

运行：py test_pose_verify.py
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

spec = importlib.util.spec_from_file_location("skyloop_poseverify", os.path.join(HERE, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec)
sys.modules["skyloop_poseverify"] = M
spec.loader.exec_module(M)

FRAME = object()
PRESSES = []


def expect(desc, cond, extra=""):
    assert cond, "%s -> 失败%s" % (desc, ("；" + extra) if extra else "")
    print("  [PASS] " + desc)


class FakePoseVision:
    """按脚本依次返回 classify_pose 的结果：(pose, sure)；脚本项为 None 表示看不清。"""

    available = True

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def classify_pose(self, frame):
        self.calls += 1
        if not self.seq:
            return None, False
        item = self.seq.pop(0)
        if item is None:
            return None, False
        pose, sure = item
        return pose, sure


# ── 打桩：按键记录 + 不真等 ──────────────────────────────────────────────
_real_key, _real_sleep = M._key, M.time.sleep
M._key = lambda sess, k, ms=80: PRESSES.append((k, ms))
M.time.sleep = lambda s: None


def run_verify(seq, target, max_round=2, vision=None, frame=FRAME):
    del PRESSES[:]
    M._vision = vision if vision is not None else FakePoseVision(seq)
    det = SimpleNamespace(latest_frame=lambda: frame)
    try:
        res = M._verify_pose_with_vision(det, None, target, max_round=max_round, settle=0)
    finally:
        pass
    return res, len(PRESSES)


try:
    print("\n[1] 两次观察一致 / 不一致")
    res, n = run_verify([("sitting", True), ("sitting", True)], "sitting")
    expect("两次都坐着、目标坐着 -> 成功且不补按", res == "sitting" and n == 0,
           "res=%s n=%d" % (res, n))

    res, n = run_verify([("sitting", True), ("standing", True)], "sitting")
    expect("两次不一致 -> 回退盲信目标、不补按", res == "sitting" and n == 0)

    res, n = run_verify([None, ("sitting", True)], "sitting")
    expect("有一次 sure=false -> 回退盲信、不补按", res == "sitting" and n == 0)

    res, n = run_verify([("sitting", True), None], "lying")
    expect("第二次看不清 -> 回退盲信、不补按", res == "lying" and n == 0)

    print("\n[2] 两次一致但没到目标 -> 补按")
    res, n = run_verify([("sitting", True), ("sitting", True),
                         ("lying", True), ("lying", True)], "lying")
    expect("目标躺着、两次都看到坐着 -> 补按 1 次后到位", res == "lying" and n == 1,
           "res=%s n=%d" % (res, n))
    expect("补按用的是动作键 3（80ms）", PRESSES == [("3", 80)], "实际 %r" % (PRESSES,))

    res, n = run_verify([("crouching", True), ("crouching", True),
                         ("sitting", True), ("sitting", True),
                         ("lying", True), ("lying", True)], "lying")
    expect("距离逐次变小 -> 连补 2 次（等于 max_round），不超过上限",
           res == "lying" and n == 2, "res=%s n=%d" % (res, n))

    print("\n[3] 补按后不该继续补的场景")
    res, n = run_verify([("sitting", True), ("sitting", True),
                         ("standing", True), ("standing", True)], "lying")
    expect("补按后距离没变小（坐着->站着，倒退）-> 立刻停，防绕圈站起",
           res == "lying" and n == 1, "res=%s n=%d" % (res, n))

    # 注意：姿势是 4 步循环，按成功一次前进距离必然 -1；所以"距离没变小"实际对应
    # 补按丢键（姿势没变）或 VLM 看错，这才是真机上最常见的失败形态。
    res, n = run_verify([("sitting", True), ("sitting", True),
                         ("sitting", True), ("sitting", True)], "lying")
    expect("补按丢键（姿势没变）-> 停，不再连补",
           res == "lying" and n == 1, "res=%s n=%d" % (res, n))

    res, n = run_verify([("sitting", True), ("sitting", True),
                         ("standing", True), ("lying", True)], "lying")
    expect("补按后两次观察不一致 -> 停", res == "lying" and n == 1)

    res, n = run_verify([("sitting", True), ("sitting", True),
                         None, ("standing", True)], "lying")
    expect("补按后看不清 -> 停", res == "lying" and n == 1)

    res, n = run_verify([("sitting", True), ("sitting", True),
                         ("lying", True), ("lying", True)], "standing")
    expect("目标站着、实际坐着：补 1 次到躺着（循环里其实更近了）-> 继续补，符合 4 步循环语义",
           res == "standing" and n >= 1, "res=%s n=%d" % (res, n))

    print("\n[4] 边界 / 防御")
    res, n = run_verify([("dancing", True), ("dancing", True)], "sitting")
    expect("视觉返回非法姿态名 -> 不 KeyError、不补按、回退盲信",
           res == "sitting" and n == 0, "res=%s n=%d" % (res, n))

    real = M.VisionClient(enabled=False)          # available=False -> ask 直接返回 None
    res, n = run_verify([], "sitting", vision=real)
    expect("视觉不可用（真 VisionClient）-> 回退盲信、不补按", res == "sitting" and n == 0)

    res, n = run_verify([], "lying", vision=real, frame=None)
    expect("拿不到帧（frame=None）-> 回退盲信、不补按", res == "lying" and n == 0)

    print("\n[5] 返回值语义（记录当前设计）")
    cases = [
        ([("sitting", True), ("sitting", True)], "lying", "两次一致没到目标且补按失败"),
        ([("standing", True), ("sitting", True)], "crouching", "两次不一致"),
        ([None, None], "sitting", "两次都看不清"),
    ]
    all_target = True
    for seq, target, _why in cases:
        r, _n = run_verify(seq, target)
        if r != target:
            all_target = False
    expect("任何路径都返回 target_pose（=『盲信目标』：校验只决定要不要补按，不改状态）",
           all_target)
    expect("补按次数恒 <= max_round（传 1 验证上限被尊重）",
           run_verify([("sitting", True), ("sitting", True),
                       ("lying", True), ("lying", True)], "lying", max_round=1)[1] <= 1)
finally:
    M._key, M.time.sleep = _real_key, _real_sleep

print("\n全部通过")
