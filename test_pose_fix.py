# -*- coding: utf-8 -*-
"""离线验证姿势补按次数与"玩家反馈是否需要补按"的纯逻辑（不依赖真实游戏/按键）。"""
import sys

POSE_STATES = {'standing': 0, 'crouching': 1, 'sitting': 2, 'lying': 3}

def press_count(target, current):
    """从 current 到 target 需要按几次动作键3（4次一循环）。"""
    return (POSE_STATES[target] - POSE_STATES[current] + 4) % 4

def needs_correction(last_target, actual_hint):
    """玩家说实际是 actual_hint，结合最近目标 last_target 判断是否需要补按（前进/后退都算）。"""
    return POSE_STATES.get(last_target, 0) != POSE_STATES.get(actual_hint, 0)

cases = [
    # (描述, 期望, 实际)
    ("想躺、当前坐着 -> 补1次", press_count('lying', 'sitting'), 1),
    ("想躺、当前蹲着 -> 补2次", press_count('lying', 'crouching'), 2),
    ("想躺、当前站着 -> 补3次", press_count('lying', 'standing'), 3),
    ("想坐、当前站着 -> 补2次", press_count('sitting', 'standing'), 2),
    ("想坐、当前蹲着 -> 补1次", press_count('sitting', 'crouching'), 1),
    ("想蹲、当前站着 -> 补1次", press_count('crouching', 'standing'), 1),
    ("已是目标姿势 -> 0次", press_count('lying', 'lying'), 0),
    ("目标lying、玩家说还坐着 -> 需补", needs_correction('lying', 'sitting'), True),
    ("目标lying、玩家说还站着 -> 需补", needs_correction('lying', 'standing'), True),
    ("目标sitting、玩家说还坐着 -> 已到位不补", needs_correction('sitting', 'sitting'), False),
    ("目标sitting、玩家说还蹲着 -> 需补", needs_correction('sitting', 'crouching'), True),
    ("目标sitting、玩家说还躺着（后退没成功）-> 需补", needs_correction('sitting', 'lying'), True),
]

fails = 0
for desc, got, want in cases:
    ok = got == want
    if not ok:
        fails += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}（得 {got}）")

print()
if fails:
    print(f"有 {fails} 条失败")
    sys.exit(1)
print("全部通过")
