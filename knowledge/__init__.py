# -*- coding: utf-8 -*-
"""
sky-with-you 本地知识库
纯本地、零封号风险。提供地图/物品/动作的查询，以及按玩家消息检索相关常识。

用法：
    from knowledge import query, core_notes
    snippets = query("我们坐下吧")      # → 相关知识文本片段
    notes = core_notes()                # → 常驻的操作约定
"""

from . import maps, items, actions


# ── 单条格式化 ──
def _format_map(name, info):
    out = [f"【地图·{name}】{info['description']}（难度：{info['difficulty']}）"]
    if info.get("visual_features"):
        out.append("画面特征：" + "、".join(info["visual_features"]))
    if info.get("belongs_to"):
        out.append(f"属于：{info['belongs_to']}")
    if info.get("connected"):
        out.append("通往：" + "、".join(info["connected"]))
    return "\n".join(out)


def _format_item(name, info):
    return (f"【物品·{name}】（{info['type']}）获取：{info['acquisition']}；"
            f"用途：{info['usage']}")


def _format_interaction(name, info):
    key = f"按键{info['key']}，" if info.get("key") else ""
    return f"【互动·{name}】{key}{info['how']}"


# ── 按玩家消息检索相关常识 ──
def query(text, limit=6):
    """返回与文本相关的知识片段（list[str]）。只匹配名称，避免过度联想。"""
    text = (text or "").strip()
    hits = []

    for m in maps.get_all_maps():
        aliases = [m] + [a for a, std in maps._MAP_ALIASES.items() if std == m]
        if any(a and a in text for a in aliases):
            hits.append(_format_map(m, maps.MAPS[m]))

    for it in items.get_all_items():
        names = [it] + [a for a, std in items._ITEM_ALIASES.items() if std == it]
        if any(n and n in text for n in names):
            hits.append(_format_item(it, items.ITEMS[it]))

    for xname, xinfo in actions.INTERACTIONS.items():
        extra = ("拥抱",) if xname == "抱抱" else ()
        if xname in text or any(e in text for e in extra):
            hits.append(_format_interaction(xname, xinfo))

    # 姿势类关键词
    if any(k in text for k in ("蹲下", "蹲", "坐下", "坐着", "躺下", "躺", "姿势", "站起来")):
        hits.append(_pose_block())

    # 回遇境（动作流程）
    if "回遇境" in text or "回遇" in text or "回家" in text:
        hits.append("【动作·回遇境】按快捷栏第5格（数字5）→ 弹窗用方向键选择、"
                    "空格确认 → 等待过场，途中不要再按空格。")

    return hits[:limit]


def _pose_block():
    return ("【姿势·数字3循环】反复按同一个键 3：按1次蹲、按2次坐、按3次躺、按4次回到站立。"
            "牵手或互动后姿势会重置为站立，需要重新摆。")


def core_notes():
    """常驻的操作约定（状态联动），list[str]。"""
    return list(actions.STATE_NOTES)


# ── 直通查询 ──
def get_map(name):
    return maps.get_map(name)


def get_item(name):
    return items.get_item(name)


def get_pose(name):
    return actions.get_pose(name)


def pose_press_count(current, target):
    return actions.pose_press_count(current, target)
