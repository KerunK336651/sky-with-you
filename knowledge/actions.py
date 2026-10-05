# -*- coding: utf-8 -*-
"""
sky-withyou 本地知识库 · 动作 / 姿势 / 按键
内容来自与玩家（珂珂）反复真机确认的操作约定。
"""

# ── 姿势：数字 3 循环（每按一次切换下一态，第4次回到站立）──
POSES = {
    "standing":   {"name": "站立", "press_3": 0, "next": "crouching"},
    "crouching":  {"name": "蹲",   "press_3": 1, "next": "sitting"},
    "sitting":    {"name": "坐",   "press_3": 2, "next": "lying"},
    "lying":      {"name": "躺",   "press_3": 3, "next": "standing"},
}
POSE_ORDER = ["standing", "crouching", "sitting", "lying"]

# 从当前姿势到目标姿势需要按几次 3（沿循环向前）
def pose_press_count(current, target):
    if current not in POSE_ORDER or target not in POSE_ORDER:
        return None
    return (POSE_ORDER.index(target) - POSE_ORDER.index(current)) % len(POSE_ORDER)


# ── 快捷栏（数字键）──
HOTBAR = {
    1: {"name": "点火", "key": "1"},
    2: {"name": "鞠躬", "key": "2"},
    3: {"name": "姿势（蹲/坐/躺循环）", "key": "3"},
    5: {"name": "回遇境", "key": "5"},
}


# ── 好友互动（多在好友树内）──
INTERACTIONS = {
    "牵手": {"key": "F", "how": "靠近好友看到牵手图标时按 F；也可在好友树里选择"},
    "打开好友树": {"key": "F", "how": "靠近好友看到好友树入口图标时按 F，游戏会自动关掉聊天框并打开好友树"},
    "击掌": {"key": None, "how": "在好友树里选择对应节点"},
    "抱抱": {"key": None, "how": "在好友树里选择，需先解锁；对方也可发起抱抱请求（也叫拥抱）"},
    "跟随": {"key": None, "how": "在好友树里选择，跟随图标不是牵手，点了会跟在后面"},
    "传送": {"key": None, "how": "在好友树里选择，可传送到好友身边"},
}


# ── 常用按键功能 ──
KEYS = {
    "C": "打开/关闭聊天面板",
    "F": "确认图标提示的互动（牵手、打开好友树等）",
    "空格": "确认（弹窗默认确认键）",
    "回车": "激活聊天输入框 / 发送消息",
    "ESC": "退出/关闭；但普通游戏界面绝对不要按 ESC（会打开设置菜单），只在好友树/弹窗等明确需要退出时用",
    "WASD": "移动角色",
    "方向键": "菜单选择（回遇境弹窗、按 E 打开的界面等，不是 WASD）",
    "E": "打开表情/动作界面",
}


# 重要的状态联动约定（供 AI 理解，避免误判）
STATE_NOTES = [
    "牵手或做其他互动动作后，姿势会被重置为站立，需要重新摆姿势。",
    "坐下后如果移动或牵手，牵手会断开，姿势也会回到站立。",
    "回遇境弹窗只有两个按钮时，默认焦点在“确定/传送”，用方向键选择、空格确认。",
    "回遇境传送过程中不要再次按空格，否则会取消；确认后要等待过场动画。",
    "跟随图标和牵手图标不同：跟随点了只是跟在后面，不会牵手。",
]


def get_pose(name):
    return POSES.get(name)


def get_hotbar(slot):
    return HOTBAR.get(slot)
